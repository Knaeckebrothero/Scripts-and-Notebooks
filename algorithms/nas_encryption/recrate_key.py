from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP, AES
from Crypto.Util.Padding import pad
import base64
import random


def gen_random_key(length):
    chars = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ~!@#$%^&*()_+-/"
    key = []
    while length > 0:
        # Include the off-by-one error as in the original JS (can lead to 'None' in output)
        key.append(chars[random.randint(0, len(chars))] if random.randint(0, len(chars)) < len(chars) else None)
        length -= 1
    return ''.join([k for k in key if k is not None])  # Filter out None values if any

# Example usage
random_key = gen_random_key(501)
print(random_key)


# Load or set up your RSA public key
key = RSA.importKey(public_key)  # Assuming public_key is loaded correctly
cipher_rsa = PKCS1_OAEP.new(key)

# Random AES key generation
aes_key = gen_random_key(32)  # Ensure the key size matches your AES mode requirements

# Encrypt the AES key with RSA
encrypted_aes_key = cipher_rsa.encrypt(aes_key)
b64_encrypted_aes_key = base64.b64encode(encrypted_aes_key)

# Now setup AES encryption
cipher_aes = AES.new(aes_key, AES.MODE_CBC)  # Assuming CBC mode; adjust as per actual
data_to_encrypt = "username:password"
encrypted_data = cipher_aes.encrypt(pad(data_to_encrypt.encode(), AES.block_size))
b64_encrypted_data = base64.b64encode(encrypted_data)

# Prepare final payload
final_payload = {
    'rsa': b64_encrypted_aes_key.decode(),
    'aes': b64_encrypted_data.decode()
}
