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
from typing import Dict,List,Union
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
from stream_helper.schema import SSEData,ContentTypeEnum,ReturnTypeEnum,OutputModeEnum,ContextModeEnum,MessageActionInfo,MessageActionItem,ReplyContentType,ContentTypeInReplyEnum,ReplyTypeInReplyEnum
from browser_use.filesystem.file_system import FileSystem
from browser_agent.upload import UploadService
from browser_agent.upload import filter_file_by_time
from stream_helper.schema import FileChangeInfo,FileChangeType,FileChangeData
import base64
from datetime import datetime
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
    start_time:int = int(datetime.now().timestamp() * 1000)
    
def genSSEData(stream_id:str,
               content:str,
               content_type:ContentTypeEnum = ContentTypeEnum.TEXT,
               return_type:ReturnTypeEnum = ReturnTypeEnum.MODEL,
               output_mode:OutputModeEnum = OutputModeEnum.STREAM,
               is_last_msg:bool = False,
               is_finish:bool = False,
               is_last_packet_in_msg:bool = False,
               message_title:str = None,
               context_mode:ContextModeEnum = ContextModeEnum.NOT_IGNORE,
               response_for_model:str = None,
               ext:Dict[str,str] = None,
               card_body:str = None,
               reply_content_type:Optional[ReplyContentType] = None)->SSEData:
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
        reply_content_type = reply_content_type
    )
def convert_ws_url(original_url: str) -> str:
    """
    将本地开发环境的 WebSocket URL 转换为生产环境的 URL 格式。
    
    示例输入:
        'ws://127.0.0.1:8001/v1/browsers/devtools/browser/77a025af-5483-46e2-ac57-9360812e4608?faasInstanceName=vefaas-duxz5kfb-jjecq2yuvf-d340et34rnmvf4b2ugd0-sandbox'
    
    示例输出:
        'ws://bots-sandbox.bytedance.net/api/sandbox/coze_studio/proxy/v1/browsers/devtools/browser/77a025af-5483-46e2-ac57-9360812e4608'
    """
    # 提取 UUID 部分（从路径中截取）
    uuid_part = original_url.split("/v1/browsers/devtools/browser/")[1].split("?")[0]
    
    # 构建新 URL
    new_url = (
        "ws://bots-sandbox.bytedance.net"
        "/ws/sandbox/coze_studio/proxy"
        f"/v1/browsers/devtools/browser/{uuid_part}"
    )
    return new_url

async def get_data_files(directory: str | Path) -> List[Dict[str, str]]:
    path = Path(directory)
    if not path.is_dir():
        raise ValueError(f"'{directory}' is not a valid directory")
    result = []
    for file in path.iterdir():
        if file.is_file():
            try:
                with open(file, 'rb') as f:
                    content = f.read()
                base64_encoded = base64.b64encode(content).decode('ascii')
                result.append({
                    "name": file.name,
                    "content": base64_encoded
                })
            except Exception as e:
                result.append({
                    "name": file.name,
                    "content": f"[ERROR READING FILE: {str(e)}]".encode()
                })
    
    return result

async def RunBrowserUseAgent(ctx: RunBrowserUseAgentCtx) -> AsyncGenerator[SSEData, None]:
    task_id = str(uuid.uuid4())
    event_queue = asyncio.Queue(maxsize=100)
    base_tmp = tempfile.gettempdir()  # e.g., /tmp on Unix
    file_system_path = os.path.join(base_tmp, f'browser_use_agent_{task_id}')
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
            async with aiohttp.ClientSession() as session:
                get_url = f"{browser_wrapper.endpoint}/v1/browsers/"
                async with session.get(url = get_url, 
                                       timeout=aiohttp.ClientTimeout(total=30),
                                       headers={
                                            'x-sandbox-taskid':ctx.conversation_id,
                                            'x-tt-env':'ppe_coze_sandbox',
                                            'x-use-ppe':'1',
                                        }) as response:
                    if response.status == 200:
                        reader = response.content
                        browser_info = json.loads(await reader.read())
                        cdp_url = convert_ws_url(browser_info['ws_url'])
                        logging.info(f"[{task_id}] Retrieved remote CDP URL: {cdp_url}")
                    else:
                        error_text = await response.text()
                        raise Exception(f"Failed to get browser info. Status: {response.status}, Error: {error_text}")
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
            highlight_elements=False,
            wait_between_actions=1,
            
            headers={
                'x-sandbox-taskid':ctx.conversation_id,
                'x-tt-env':'ppe_coze_sandbox',
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
                if action_name == 'wait_for_login':
                    islogin = True
            data = ''
            content_type = ContentTypeInReplyEnum.TXT
            if islogin:
                data = data + MessageActionInfo(actions=[MessageActionItem()]).model_dump_json()
                content_type = ContentTypeInReplyEnum.ACTION_INFO
            else: 
                data = data + model_output.next_goal
            await event_queue.put(genSSEData(
                stream_id=ctx.conversation_id,
                content=data,
                reply_content_type= ReplyContentType(content_type=content_type)
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
        if ctx.upload_service:
            try:
                fileList = await get_data_files(file_system.get_dir())
                for file in fileList:
                    file_content = await file_system.read_file(file_system_path+'/browseruse_agent_data/'+ file['name'],external_file=True)
                    file_new_name = f'{task_id}/{file["name"]}'
                    await ctx.upload_service.upload_file(file_content=file_content,file_name=file_new_name)
            except Exception as e:
                logging.error(f"[{task_id}] Error in upload file: {e}")
            try:
                file_items = await ctx.upload_service.list_file()
                file_change_info = FileChangeInfo(file_change_list=[],err_list=[])
                file_items =await filter_file_by_time(file_items,ctx.start_time)
                if len(file_items) > 0:
                    for file_item in file_items:
                        change_type = FileChangeType.CREATE
                        if file_item.create_time != file_item.update_time:
                            change_type = FileChangeType.UPDATE
                        file_change_info.file_change_list.append(FileChangeData(
                            file_name= file_item.file_name,
                            change_type=change_type,
                            uri= file_item.file_uri,
                            url= file_item.file_url,
                        ))
                    logging.info(f"filter file by time success, file_change_info: {file_change_info.model_dump_json()}")
                    file_pack = SSEData(
                        stream_id=ctx.conversation_id,
                        reply_content_type=ReplyContentType(content_type=ContentTypeInReplyEnum.FILE_CHANGE_INFO,reply_type=ReplyTypeInReplyEnum.ANSWER),
                        content = file_change_info.model_dump_json())
                    yield file_pack
            except Exception as e:
                logging.error(f"[{task_id}] Error in get file change info: {e}")
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
                result = [
                    [item.extracted_content for item in history_item.result 
                     if hasattr(item, "extracted_content")]
                    for history_item in result.history
                ]
                final_result = "\n".join(
                    item
                    for sublist in result
                    for item in sublist
                    if isinstance(item, str)
                )
            if final_result:
                logging.info(f"[{task_id}] final_result: {final_result}")
                completion_event = genSSEData(
                    stream_id=ctx.conversation_id,
                    content= final_result,
                    return_type=ReturnTypeEnum.MODEL,
                    response_for_model=final_result,
                    content_type=ContentTypeEnum.TEXT,
                    output_mode=OutputModeEnum.STREAM,
                    is_finish= True,
                    is_last_msg=True,
                    is_last_packet_in_msg=True,
                    reply_content_type=ReplyContentType(content_type=ContentTypeInReplyEnum.TXT,reply_type=ReplyTypeInReplyEnum.ANSWER)
                )
                yield completion_event

        logging.info(f"[{task_id}] Task completed successfully")

    except Exception as e:
        logging.error(f"[{task_id}] Agent execution failed: {e}")
        yield genSSEData(
            stream_id=ctx.conversation_id,
            content=f'err:{e}',
            is_finish=True,
            is_last_msg=True,
            is_last_packet_in_msg=True,
            return_type=ReturnTypeEnum.MODEL,
            response_for_model=f'err:{e}',
            reply_content_type=ReplyContentType(content_type=ContentTypeInReplyEnum.TXT,reply_type=ReplyTypeInReplyEnum.ANSWER)
        )


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



