from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


def upload_to_youtube(refresh_token, client_id, client_secret, file_path, title, description, yt_format="shorts", thumb_path=None, first_comment=None):
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret
    )
    creds.refresh(Request())
    youtube = build('youtube', 'v3', credentials=creds)

    final_desc = description or ""
    if yt_format == "shorts" and "#shorts" not in final_desc.lower():
        final_desc = f"{final_desc}\n\n#shorts".strip()

    body = {
        'snippet': {
            'title': title[:100],
            'description': final_desc,
            'categoryId': '22'
        },
        'status': {
            'privacyStatus': 'public',
            'selfDeclaredMadeForKids': False
        }
    }

    media = MediaFileUpload(file_path, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part=','.join(body.keys()), body=body, media_body=media)

    response = None
    while response is None:
        _, response = request.next_chunk()

    video_id = response.get('id')

    # Özel Thumbnail varsa yükle
    if thumb_path and video_id:
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumb_path)
            ).execute()
            print(f"  🖼️ Özel YouTube Thumbnail yüklendi.")
        except Exception as e:
            print(f"  ⚠️ Thumbnail yükleme uyarısı (Kanal doğrulaması gerekebilir): {e}")

    # İlk Yorum (First Comment) varsa at
    if first_comment and first_comment.strip() and video_id:
        try:
            youtube.commentThreads().insert(
                part="snippet",
                body={
                    "snippet": {
                        "videoId": video_id,
                        "topLevelComment": {
                            "snippet": {"textOriginal": first_comment.strip()}
                        }
                    }
                }
            ).execute()
            print(f"  💬 YouTube ilk yorum eklendi.")
        except Exception as e:
            print(f"  ⚠️ YouTube yorum uyarısı: {e}")

    return video_id
