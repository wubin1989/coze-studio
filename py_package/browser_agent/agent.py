from pydantic import BaseModel
from browser_agent.index import RunBrowserUseAgentCtx,RunBrowserUseAgent,LLMConfig
from typing import AsyncGenerator,Dict
from abc import ABC, abstractmethod
from datetime import datetime
from stream_helper.schema import SSEData
class BrowserAgentBase(BaseModel,ABC):
    query: str
    conversation_id: str = ''
    cookies: str = ''
    llm_config: LLMConfig
    browser_session_endpoint: str
    endpoint_header: Dict[str, str] = {}
    max_steps: int = 20
    system_prompt: str = None
    start_time:datetime
    @abstractmethod
    async def get_cookies(self)->str:
        pass
    
    @abstractmethod
    async def save_cookies(self,cookies:str):
        pass
    
    @abstractmethod
    async def get_cookies_from_storage(self)->str:
        pass

    @abstractmethod
    async def get_token(self)->str:
        pass
    
    @abstractmethod
    async def get_llm_config(self)->LLMConfig:
        pass
    
    @abstractmethod
    async def get_system_prompt(self)->str:
        pass
    
    @abstractmethod
    async def init_remote_browser(self):
        pass
    
    @abstractmethod
    async def get_work_dir(self)->str:
        pass
    
    async def run(self)->AsyncGenerator[SSEData,None]:
        pass

