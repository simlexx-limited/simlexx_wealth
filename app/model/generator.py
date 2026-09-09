import uuid

def id_generator():
    id_gen = str(uuid.uuid4()).split("-")
    return id_gen[2] + id_gen[4]
