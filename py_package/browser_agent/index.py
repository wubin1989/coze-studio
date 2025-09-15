# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# Licensed under the 【火山方舟】原型应用软件自用许可协议
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at 
#     https://www.volcengine.com/docs/82379/1433703
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from typing import Dict
import tempfile
import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import AsyncGenerator,Optional
import aiohttp
import aiohttp
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from browser_use.llm import ChatOpenAI
from pydantic import BaseModel, Field
from browser_use.browser.views import BrowserStateSummary
from browser_agent.browser import start_local_browser,BrowserWrapper
from browser_agent.browser_use_custom.controller.service import MyController
from browser_agent.utils import enforce_log_format
from browser_use import Agent
from browser_agent.browser_use_custom.i18n import _, set_language
from browser_use.agent.views import (
	AgentOutput,
)
from browser_use.llm.base import BaseChatModel
from browser_use import Agent, BrowserProfile, BrowserSession
from stream_helper.schema import SSEData,ContentTypeEnum,ReturnTypeEnum,OutputModeEnum,ContextModeEnum,StepInfo,MessageActionInfo,MessageActionItem
from browser_use.filesystem.file_system import FileSystem
from browser_agent.upload import UploadService
app = FastAPI()
load_dotenv()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["x-faas-instance-name"],
)

llm_openai = "openai"
llm_deepseek = "deepseek"
llm_ark = "ark"
llm_name = llm_ark


# Global variable to track the port
CURRENT_CDP_PORT = 9222

browser_session_endpoint = None
set_language(os.getenv("LANGUAGE", "en"))


class Message(BaseModel):
    role: str
    content: str


class Messages(BaseModel):
    messages: list[Message]


class TaskRequest(BaseModel):
    task: str

import logging

class LLMConfig(BaseModel):
    llm_type: str
    model_id: str
    api_key: str
    extract_model_id: str

class RunBrowserUseAgentCtx(BaseModel):
    query: str
    conversation_id: str
    llm: BaseChatModel
    browser_session_endpoint: str
    max_steps: int = 20
    system_prompt: str | None = None
    upload_service:Optional[UploadService] = None
    
def genSSEData(stream_id:str,content:str,content_type:ContentTypeEnum = ContentTypeEnum.TEXT,return_type:ReturnTypeEnum = ReturnTypeEnum.MODEL,output_mode:OutputModeEnum = OutputModeEnum.STREAM,is_last_msg:bool = False,is_finish:bool = False,is_last_packet_in_msg:bool = False,message_title:str = None,context_mode:ContextModeEnum = ContextModeEnum.NOT_IGNORE,response_for_model:str = None,ext:Dict[str,str] = None,card_body:str = None)->SSEData:
    return SSEData(
        stream_id = stream_id,
        content = content,
        content_type = content_type,
        return_type = return_type,
        output_mode = output_mode,
        is_last_msg = is_last_msg,
        is_finish = is_finish,
        is_last_packet_in_msg = is_last_packet_in_msg,
        message_title = message_title,
        context_mode = context_mode,
        response_for_model = response_for_model,
        ext = ext,
        card_body = card_body,
    )

async def RunBrowserUseAgent(ctx: RunBrowserUseAgentCtx) -> AsyncGenerator[SSEData, None]:
    task_id = str(uuid.uuid4())
    event_queue = asyncio.Queue(maxsize=100)
    base_tmp = tempfile.gettempdir()  # e.g., /tmp on Unix
    file_system_path = os.path.join(base_tmp, f'browser_use_agent_666')
    file_system = FileSystem(base_dir=file_system_path)
    # 初始化日志
    logging.info(f"RunBrowserUseAgent with query: {ctx.query},task_id:{task_id}")
    
    # 浏览器初始化
    try:
        if ctx.browser_session_endpoint == "" or ctx.browser_session_endpoint is None:
            global CURRENT_CDP_PORT
            CURRENT_CDP_PORT += 1
            current_port = CURRENT_CDP_PORT
            browser_wrapper = await start_local_browser(current_port)
        else:
            browser_wrapper = BrowserWrapper(None, None, None, 'id', ctx.browser_session_endpoint)
    except Exception as e:
        logging.error(f"[Failed to initialize browser: {e}")
        yield "error"
        return

    # CDP URL 获取
    cdp_url = None
    try:
        if browser_wrapper.remote_browser_id:
            logging.info(f"Starting task with remote browser")
            async with aiohttp.ClientSession() as session:
                cdp_url = f"{browser_wrapper.endpoint}/v1/browsers/"
        else:
            current_port = CURRENT_CDP_PORT
            logging.info(f"Starting task with local browser on port: {current_port}")
            cdp_url = f"http://127.0.0.1:{current_port}"
    except Exception as e:
        logging.error(f"Error getting browser URL: {e}")
        yield "error"
        return

    # 目录创建
    base_dir = os.path.join("videos", task_id)
    snapshot_dir = os.path.join(base_dir, "snapshots")
    Path(snapshot_dir).mkdir(parents=True, exist_ok=True)
    file_dir = os
    browser_session = None
    agent = None
    agent_task = None

    try:
        # 浏览器会话配置
        headless = False if ctx.browser_session_endpoint != "" else True
        browser_profile = BrowserProfile(
            headless=headless,
            disable_security=True,
            highlight_elements=True,
            wait_between_actions=1,
            headers={
                'x-sandbox-taskid':ctx.conversation_id,
                'x-tt-env':'ppe_agent_ide',
                'x-use-ppe':'1',
            },
        )
        browser_session = BrowserSession(
            browser_profile=browser_profile,
            cdp_url=cdp_url,
        )
        logging.info(f"[{task_id}] Browser initialized with CDP URL: {cdp_url}")

        # 回调函数定义
        async def new_step_callback_wrapper(browser_state_summary: BrowserStateSummary, 
                                          model_output: AgentOutput, 
                                          step_number: int):
            islogin = False
            for ac in model_output.action:
                action_data = ac.model_dump(exclude_unset=True)
                action_name = next(iter(action_data.keys()))
                content = ''
                file_name = ''
                if action_name == 'wait_for_login':
                    islogin = True
                if action_name == 'write_file':
                    content = action_data[action_name]['content']
                    file_name = f'{task_id}/{action_data[action_name]["file_name"]}'
                if action_name == 'replace_file_str':
                    file_name = f'{task_id}/{action_data[action_name]["file_name"]}'
                    file_obj = file_system.get_file({action_data[action_name]["file_name"]})
                    origin_content = file_obj.read()
                    content = origin_content.replace(action_data[action_name]["old_str"], action_data[action_name]["new_str"])
                if ctx.upload_service:
                    if action_name == 'write_file' or action_name == 'replace_file_str':
                        await ctx.upload_service.upload_file(file_content=content,file_name=file_name)
            data = ''
            if islogin:
                data = data + MessageActionInfo(actions=[MessageActionItem()]).model_dump_json()
            else: 
                data = data + StepInfo(step_number=(step_number-1),goal=model_output.next_goal).model_dump_json()
            await event_queue.put(genSSEData(
                stream_id=task_id,
                content=data
            ))

        # Agent 创建
        agent = Agent(
            task=ctx.query,
            llm=ctx.llm,
            tool_calling_method=os.getenv("ARK_FUNCTION_CALLING", "function_calling").lower(),
            browser_session=browser_session,
            register_new_step_callback=new_step_callback_wrapper,
            use_vision=os.getenv("ARK_USE_VISION", "False").lower() == "true",
            use_vision_for_planner=os.getenv("ARK_USE_VISION", "False").lower() == "true",
            page_extraction_llm=ctx.llm,
            file_system_path=file_system_path,
            controller=MyController(),
            override_system_message=ctx.system_prompt,
        )

        logging.info(f"[{task_id}] Agent initialized and ready to run")

        # 启动 Agent 任务
        agent_task = asyncio.create_task(agent.run(20))
        logging.info(f"[{task_id}] Agent started running")

        # 事件流生成
        while True:
            try:
                # 等待事件或检查任务完成
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.5)
                    yield event
                    
                    # 检查是否应该结束
                    if event == "error" or (isinstance(event, dict) and event.get("status") == "completed"):
                        break
                        
                except asyncio.TimeoutError:
                    # 检查 Agent 任务是否完成
                    if agent_task.done():
                        break
                    continue
                    
            except Exception as e:
                logging.error(f"[{task_id}] Error in event streaming: {e}")
                break

        # 等待 Agent 任务完成
        if agent_task and not agent_task.done():
            await agent_task

        # 获取最终结果
        result = await agent_task if agent_task else None
        if result:
            final_result = None
            for history_item in reversed(result.history):
                for result_item in history_item.result:
                    if hasattr(result_item, "is_done") and result_item.is_done:
                        final_result = result_item.extracted_content
                        break
                if final_result:
                    break

            if not final_result:
                final_result = [
                    [item.extracted_content for item in history_item.result 
                     if hasattr(item, "extracted_content")]
                    for history_item in result.history
                ]
                completion_event = genSSEData(
                    stream_id=task_id,
                    content= 'done'
                )
                yield completion_event

        logging.info(f"[{task_id}] Task completed successfully")

    except Exception as e:
        logging.error(f"[{task_id}] Agent execution failed: {e}")
        yield "error"
        
    finally:
        # 清理资源
        async def cleanup():
            try:
                if agent:
                    agent.stop()
                if agent_task and not agent_task.done():
                    agent_task.cancel()
                    try:
                        await agent_task
                    except asyncio.CancelledError:
                        pass
                if browser_session:
                    await browser_session.close()
                if browser_wrapper:
                    await browser_wrapper.stop()
            except Exception as e:
                logging.error(f"[{task_id}] Failed during cleanup: {e}")
        
        # 非阻塞清理
        asyncio.create_task(cleanup())  



@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.post("/tasks")
async def sse_task(request: Messages):
    task_id = str(uuid.uuid4())
    
    # 提取用户消息
    prompt = ""
    for message in request.messages:
        if message.role == "user":
            prompt = message.content
            logging.debug(f"Found user message: {prompt}")
            break

    logging.info(f"[{task_id}] Starting SSE task with prompt: {prompt}")

    async def generate_sse_events():
        try:
            # 创建SSE格式的生成器
            async for event in RunBrowserUseAgent(ctx=RunBrowserUseAgentCtx(
                query=prompt,
                conversation_id="",
                cookie="",
                llm_config=LLMConfig(
                ),
                browser_session_endpoint="http://127.0.0.1:8001/v1/browsers",
                max_steps=20
            )):
                # 确保事件是SSE格式
                if isinstance(event, str):
                    # 如果是字符串，直接作为数据发送
                    yield f"data: {event}\n\n"
                elif isinstance(event, dict):
                    # 如果是字典，转换为JSON格式
                    event_json = json.dumps(event, ensure_ascii=False)
                    yield f"data: {event_json}\n\n"
                else:
                    # 其他类型转换为字符串
                    yield f"data: {str(event)}\n\n"
                    
        except Exception as e:
            logging.error(f"[{task_id}] Error in SSE generation: {e}")
            # 发送错误事件
            error_event = json.dumps({
                "task_id": task_id,
                "status": "error",
                "error": str(e)
            }, ensure_ascii=False)
            yield f"data: {error_event}\n\n"
        finally:
            # 可选：发送结束标记
            yield f"data: {json.dumps({'task_id': task_id, 'status': 'stream_completed'}, ensure_ascii=False)}\n\n"

    try:
        return StreamingResponse(
            generate_sse_events(),
            media_type="text/event-stream",
        )
    except Exception as e:
        logging.error(f"[{task_id}] Error creating StreamingResponse: {e}")
        # 返回错误响应
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to create SSE stream", "task_id": task_id}
        )      
    
if __name__ == "__main__":
    enforce_log_format()
    uvicorn.run(app, host="0.0.0.0", port=8000)


