import uuid


def shortuuid():
    return str(uuid.uuid4())[:8]
