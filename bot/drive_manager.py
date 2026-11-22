import io
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload


class DriveManager:
    def __init__(self, service_account_json_str, archive_root_id):
        info = json.loads(service_account_json_str)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=['https://www.googleapis.com/auth/drive']
        )
        self.service = build('drive', 'v3', credentials=creds)
        self.archive_root_id = archive_root_id

    def get_file_metadata(self, file_id):
        return self.service.files().get(
            fileId=file_id, fields='id, name, mimeType'
        ).execute()

    def download_file(self, file_id, destination_path):
        request = self.service.files().get_media(fileId=file_id)
        fh = io.FileIO(destination_path, 'wb')
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        fh.close()
        return destination_path

    def upload_temp_jpeg(self, local_path, file_name):
        """PNG/WebP'den dönüştürülen geçici JPEG dosyasını Drive'a yükler."""
        metadata = {
            'name': file_name,
            'parents': [self.archive_root_id],
        }
        media = MediaFileUpload(local_path, mimetype='image/jpeg', resumable=False)
        res = self.service.files().create(
            body=metadata, media_body=media, fields='id'
        ).execute()
        return res['id']

    def set_public_link(self, file_id):
        permission = {'type': 'anyone', 'role': 'reader'}
        res = self.service.permissions().create(fileId=file_id, body=permission).execute()
        return f"https://drive.google.com/uc?export=download&id={file_id}", res['id']

    def remove_public_link(self, file_id, permission_id):
        try:
            self.service.permissions().delete(fileId=file_id, permissionId=permission_id).execute()
        except Exception as e:
            print(f"⚠️ İzin kaldırma uyarısı: {e}")

    def move_to_archive(self, file_id):
        if not file_id:
            return
        file_meta = self.service.files().get(fileId=file_id, fields='parents').execute()
        previous_parents = ",".join(file_meta.get('parents', []))
        self.service.files().update(
            fileId=file_id,
            addParents=self.archive_root_id,
            removeParents=previous_parents,
            fields='id, parents'
        ).execute()

    def delete_file_permanently(self, file_id):
        try:
            self.service.files().delete(fileId=file_id).execute()
            return True
        except Exception as e:
            print(f"⚠️ Arşiv silme hatası ({file_id}): {e}")
            return False
