"""Authenticated heartbeat entry point; the only handler an external source should invoke."""
import os
import time
import boto3
from botocore.exceptions import ClientError

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
DEVICE_KEY = os.environ["DEVICE_KEY"]
TTL_SECONDS = 4 * 60 * 60


def lambda_handler(event, context):
    # Never accept an arbitrary deadline or clock from the external caller.
    # A successful heartbeat always renews the full four-hour lease.
    now = int(time.time())
    try:
        result = TABLE.update_item(
            Key={"pk": DEVICE_KEY},
            UpdateExpression=(
                "SET #s = :armed, expires_at = :expiry, last_heartbeat_at = :now, "
                "#v = if_not_exists(#v, :zero) + :one"
            ),
            ConditionExpression="attribute_not_exists(pk) OR #s IN (:armed, :tripped)",
            ExpressionAttributeNames={"#s": "status", "#v": "version"},
            ExpressionAttributeValues={
                ":armed": "ARMED", ":tripped": "TRIPPED", ":expiry": now + TTL_SECONDS,
                ":now": now, ":zero": 0, ":one": 1,
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return {"accepted": False, "reason": "Cycle in progress or operator intervention required"}
        raise
    item = result["Attributes"]
    return {
        "accepted": True, "device": DEVICE_KEY,
        "expires_at": int(item["expires_at"]),
        "version": int(item["version"]),
        "status": item["status"],
    }
