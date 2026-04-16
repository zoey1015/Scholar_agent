"""
MinIO 对象存储服务

负责 PDF 文件的上传、下载、删除。
"""

import io
import logging
from typing import Optional

from minio import Minio
from minio.error import S3Error

from backend.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()


class MinioService:
    """MinIO 文件存储操作封装"""

    def __init__(self):
        self.client = Minio(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=False,  # 开发环境用 HTTP
        )
        self.bucket = settings.minio_bucket
        self._ensure_bucket()

    def _ensure_bucket(self):
        """确保存储桶存在，不存在则创建"""
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
                logger.info(f"Created MinIO bucket: {self.bucket}")
        except S3Error as e:
            logger.error(f"MinIO bucket check failed: {e}")

    def upload_file(
        self,
        file_data: bytes,
        object_name: str,
        content_type: str = "application/pdf",
    ) -> str:
        """
        上传文件到 MinIO

        Args:
            file_data: 文件二进制内容
            object_name: 存储路径（如 "papers/uuid.pdf"）
            content_type: MIME 类型

        Returns:
            object_name: 存储路径，后续用这个路径下载
        """
        try:
            data_stream = io.BytesIO(file_data)
            self.client.put_object(
                bucket_name=self.bucket,
                object_name=object_name,
                data=data_stream,
                length=len(file_data),
                content_type=content_type,
            )
            logger.info(f"Uploaded to MinIO: {self.bucket}/{object_name} ({len(file_data)} bytes)")
            return object_name
        except S3Error as e:
            logger.error(f"MinIO upload failed: {e}")
            raise

    def download_file(self, object_name: str) -> bytes:
        """
        从 MinIO 下载文件

        Args:
            object_name: 存储路径

        Returns:
            文件二进制内容
        """
        try:
            response = self.client.get_object(self.bucket, object_name)
            data = response.read()
            response.close()
            response.release_conn()
            logger.info(f"Downloaded from MinIO: {self.bucket}/{object_name} ({len(data)} bytes)")
            return data
        except S3Error as e:
            logger.error(f"MinIO download failed: {e}")
            raise

    def delete_file(self, object_name: str) -> bool:
        """删除文件"""
        try:
            self.client.remove_object(self.bucket, object_name)
            logger.info(f"Deleted from MinIO: {self.bucket}/{object_name}")
            return True
        except S3Error as e:
            logger.error(f"MinIO delete failed: {e}")
            return False

    def file_exists(self, object_name: str) -> bool:
        """检查文件是否存在"""
        try:
            self.client.stat_object(self.bucket, object_name)
            return True
        except S3Error:
            return False


# 全局单例
_minio_service: Optional[MinioService] = None


def get_minio_service() -> MinioService:
    """获取 MinIO 服务单例"""
    global _minio_service
    if _minio_service is None:
        _minio_service = MinioService()
    return _minio_service
