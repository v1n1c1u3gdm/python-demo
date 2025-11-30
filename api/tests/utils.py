import json


def json_body(response):
    return json.loads(response.data.decode("utf-8"))

