
import requests
import jwt
import time
import os

SERVICE_ACCOUNT = {
  "type": "service_account",
  "project_id": "pomogay",
  "private_key_id": "e37311b2922e66bff698608fbf96617b39ba7608",
  "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDXXm3hHfXkm5N8\nfFLSm1J1RdUf5zEgADeT37dHmC9lfsNQc98R+IH8lWfpWDKdIijkFguYp8RyOXg9\no+Rw+z8mxD1GtybGK1Y6TKM0dV1y1UI0wUXs9EImZh0JZ9fkkT4ZnwUdoMJr3RP7\nPVxgdR+QiyEM/v+kLtETcKBKwkQ8SH1pGQ+zkUi5tswiD6pKAvvZIHbElXicOt8V\nh+tYNZ60jrlC11rxR6MvyaGmm29IJXvYB/nrGWE7cOsOItM/XEhauyasYz43dLEO\neMd6wDSwse7Yg13xuSdxCR6g7ZCU/1tg5UxwlJQZg3HWFD4PzXbWo4ZpPziH5DZl\n3BbHXhPBAgMBAAECggEATSERy9nNepIm5yGlDay3gq1ALt0+sB2zyb8IZdkAPGKB\n8zmUDV0IvFsLbDt8YN7fcxPcEk8e4ONpBvY/dSDdlHYaFc240qapilZw6nQPw/TQ\n8ZC4CKzPVe4i/v/UBxTm/wj+rYYpqrRxl/v5Lcg0RjE1ZHCcQAHOOZ6PoUZiZo3M\n4VRbh+1vDSAzhP12I8VvShz77xQWYsRckKMeVhakCjBTSLqN+3krIjmmizH//XYZ\n3ThUDQAHk8r0T3xdEkVwu6gHbG+wlW/gyRIo+xlUvW0ygQE7rxKinfnSCcBx/cuK\n6ogJ2+B5wbg1S778NevYdw5kGnH6FTpWMrRa3OKhewKBgQD6/ico+a5COoS/GQLN\nVu6QitdT5gEUKhF082C47/9ZP0sF95ZCIhNqqM681d59a1KQG0aW67lvW1ssNMQZ\n1WUNKd4yj41ao7R18jicw4za5oUKvaL+b35vgCRcFtwiyo4GnbbnJzm0yLpLhNAX\n14oLeHbO9Cqy9LUJY5Inks8rHwKBgQDbqldQQbXhWI25qzAG3oYtIM9kza8a0Vd6\nXQ5YSpaG5JM0O+/NbFgM1tW2m7YyU0ElhU+8LBENKXJ5DctKIAv6FCFhM9UTfz14\nvh8E2+Z46pkXoQBE+pJZ6c9aqxF93ZRGnACvLzpy6qZrrtennqIflFpUSjVGr8Xx\nYjk1EcfFHwKBgG/YWXL08CYrFYRijNEb7+sRM6r7T3fA106aNXuz1sHaZoAeOCL4\nNFbPKnETjLWu4Xe979LI8DUjLJmiWdB5OzKGebFmLsM5N+1nJrsUmOvi1V5X6w4e\neOUO4ST4Oth2Epv4I80Vua1J1VpZsLEvJyC9aZQHUg+05AdvC23/CbpZAoGBANZT\nda3Q721XwbCO3uVU6QXPJYvtRBSefQPfF8f9vrEPBLHKaUe9louwcgUiGLsxkDbT\nw+CN/nUhI5gJZXiFnCn8yjTVJelIFOpiVlGfXVhNTeJILMMg2PrxrmeA0ihEsg/S\n1rgXFKhbWtVmWvQpS3YUga4MWb8GcdP7SmYFWy1xAoGAZGqHmrAnbP6s1ePZZNdr\n2wwGsWLhjI0Co/HeN0kyOGp6JsQBWUsxYEeKxbzD9lDQAvIozvuJUHOmzkAqW514\nayDSR3mCRQppxdILwcNJP5gtKLNV161VYl6ljpcUfV6/oyg/cvfxfDeTUgHjBtT1\nyMjcDgyAejVXWSu2h9lYZMY=\n-----END PRIVATE KEY-----\n",
  "client_email": "pomogay-drive@pomogay.iam.gserviceaccount.com",
  "token_uri": "https://oauth2.googleapis.com/token"
}

FOLDER_ID = "10O5F9Q9w0Hf1lrSSVLSXviUPlyCciULM"
DB_FILENAME = "pomogay.db"

def get_access_token():
    now = int(time.time())
    payload = {
        'iss': SERVICE_ACCOUNT['client_email'],
        'scope': 'https://www.googleapis.com/auth/drive.file',
        'aud': 'https://oauth2.googleapis.com/token',
        'exp': now + 3600,
        'iat': now
    }
    signed_jwt = jwt.encode(payload, SERVICE_ACCOUNT['private_key'], algorithm='RS256')
    r = requests.post('https://oauth2.googleapis.com/token', data={
        'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
        'assertion': signed_jwt
    })
    return r.json()['access_token']

def download_db():
    """Скачивает базу с Google Диска. Возвращает True если успешно."""
    try:
        token = get_access_token()
        # Ищем файл в папке
        r = requests.get(
            'https://www.googleapis.com/drive/v3/files',
            headers={'Authorization': f'Bearer {token}'},
            params={
                'q': f"'{FOLDER_ID}' in parents and name = '{DB_FILENAME}'",
                'fields': 'files(id, name)'
            }
        )
        files = r.json().get('files', [])
        if not files:
            print('База не найдена на Диске')
            return False
        
        file_id = files[0]['id']
        # Скачиваем
        r = requests.get(
            f'https://www.googleapis.com/drive/v3/files/{file_id}',
            headers={'Authorization': f'Bearer {token}'},
            params={'alt': 'media'}
        )
        
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'pomogay.db')
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        with open(db_path, 'wb') as f:
            f.write(r.content)
        print('База загружена с Google Диска')
        return True
    except Exception as e:
        print(f'Ошибка загрузки базы: {e}')
        return False

def upload_db():
    """Загружает базу на Google Диск."""
    try:
        token = get_access_token()
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'pomogay.db')
        
        if not os.path.exists(db_path):
            print('База не найдена локально')
            return False
        
        # Ищем существующий файл
        r = requests.get(
            'https://www.googleapis.com/drive/v3/files',
            headers={'Authorization': f'Bearer {token}'},
            params={
                'q': f"'{FOLDER_ID}' in parents and name = '{DB_FILENAME}'",
                'fields': 'files(id)'
            }
        )
        files = r.json().get('files', [])
        
        if files:
            # Обновляем существующий
            file_id = files[0]['id']
            with open(db_path, 'rb') as f:
                r = requests.patch(
                    f'https://www.googleapis.com/upload/drive/v3/files/{file_id}',
                    headers={'Authorization': f'Bearer {token}'},
                    params={'uploadType': 'media'}
                )
                r.raise_for_status()  # проверяем, что нет ошибки
                # Если PATCH не сработал, используем PUT
                if r.status_code != 200:
                    r = requests.put(
                        f'https://www.googleapis.com/upload/drive/v3/files/{file_id}',
                        headers={'Authorization': f'Bearer {token}'},
                        params={'uploadType': 'media'},
                        data=f.read()
                    )
        else:
            # Создаём новый
            with open(db_path, 'rb') as f:
                r = requests.post(
                    'https://www.googleapis.com/upload/drive/v3/files',
                    headers={'Authorization': f'Bearer {token}'},
                    params={'uploadType': 'multipart'},
                    files={
                        'metadata': (None, json.dumps({
                            'name': DB_FILENAME,
                            'parents': [FOLDER_ID]
                        }), 'application/json'),
                        'file': (DB_FILENAME, f, 'application/octet-stream')
                    }
                )
        
        print('База сохранена на Google Диск')
        return True
    except Exception as e:
        print(f'Ошибка сохранения базы: {e}')
        return False
