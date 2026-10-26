"""Cloud Kasa commands. No local-network access to the plug is required."""
import asyncio
import json
import os
import boto3
from tplinkcloud import TPLinkDeviceManager

SECRETS = boto3.client("secretsmanager")
SECRET_ARN = os.environ["SECRET_ARN"]
DEVICE_ALIAS = os.environ["DEVICE_ALIAS"]


async def _run(action):
    secret = json.loads(SECRETS.get_secret_value(SecretId=SECRET_ARN)["SecretString"])
    manager = TPLinkDeviceManager(
        username=secret["username"],
        password=secret["password"],
        include_tapo=False,
        prefetch=False,
    )
    if not manager.get_token():
        raise RuntimeError("TP-Link Cloud login failed; check credentials/MFA")
    device = await manager.find_device(DEVICE_ALIAS)
    if device is None:
        raise RuntimeError(f"Kasa device alias not found: {DEVICE_ALIAS!r}")
    if action == "off":
        await device.power_off()
    elif action == "on":
        await device.power_on()
    else:
        raise ValueError("Action must be 'off' or 'on'")
    return {"command_sent": True, "action": action, "device": DEVICE_ALIAS}


def lambda_handler(event, context):
    return asyncio.run(_run(event["action"]))
