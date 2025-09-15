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
    create_time: datetime
    update_time: datetime

    @classmethod
    def from_timestamp(cls, **data):
        # 将毫秒时间戳转换为 datetime 对象
        if 'create_time' in data:
            data['create_time'] = datetime.fromtimestamp(data['create_time'] / 1000)
        if 'update_time' in data:
            data['update_time'] = datetime.fromtimestamp(data['update_time'] / 1000)

class UploadService(BaseModel,ABC):
    async def upload_file(self,headers:dict[str,str],file_content:str,file_name:str):
        pass
    async def list_file(self,headers:dict[str,str])->List[FileItem]:
        pass