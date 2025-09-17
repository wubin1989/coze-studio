from datetime import datetime
from typing import List
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
    async def upload_file(self,file_content:str,file_name:str):
        pass
    async def list_file(self)->List[FileItem]:
        pass