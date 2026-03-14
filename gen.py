from py_vapid import Vapid

v = Vapid()
v.generate_keys()
v.save_key("private_key.pem")
v.save_public_key("public_key.pem")

print("Done! Check private_key.pem and public_key.pem in your folder")