from cryptography.fernet import Fernet, InvalidToken


class MessageEncryptor:
    def __init__(self, secret_key: str):
        if not secret_key:
            raise ValueError("ENCRYPTION_KEY не установлен")
        self._fernet = Fernet(secret_key.encode("utf-8"))

    def encrypt_message(self, text: str) -> str:
        encrypted = self._fernet.encrypt(text.encode("utf-8"))
        return encrypted.decode("utf-8")

    def decrypt_message(self, encrypted_text: str) -> str:
        try:
            decrypted = self._fernet.decrypt(encrypted_text.encode("utf-8"))
            return decrypted.decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Не удалось расшифровать сообщение: некорректный токен") from exc
