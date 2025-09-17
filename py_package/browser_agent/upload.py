from datetime import datetime
from typing import List,Optional
from abc import ABC, abstractmethod
from pydantic import BaseModel
class FileItem(BaseModel):
    file_name: str
    file_type: str
    file_size: int
    file_uri: str
    file_url: str
    upload_type: str
    create_time: int
    update_time: int

class UploadService(BaseModel,ABC):
    headers:Optional[dict[str,str]] = {}
    async def upload_file(self,file_content:str,file_name:str):
        pass
    async def list_file(self,headers:dict[str,str])->List[FileItem]:
        pass