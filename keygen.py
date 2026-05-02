from cryptography.fernet import Fernet

# Генерируем ключ
key = Fernet.generate_key()

# Выводим его на экран
print("Твой секретный ключ (скопируй его целиком):")
print(key.decode())