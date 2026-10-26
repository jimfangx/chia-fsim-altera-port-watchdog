"""Periodic watchdog check and atomic cycle claim/finish actions."""
import json
import os
import time
import boto3
from botocore.exceptions import ClientError

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
DEVICE_KEY = os.environ["DEVICE_KEY"]
STEP_FUNCTIONS = boto3.client("stepfunctions")
MACHINE_ARN = os.environ["MACHINE_ARN"]


def _change_status(version, expected_status, next_status, timestamp_attribute):
    try:
        TABLE.update_item(
            Key={"pk": DEVICE_KEY},
            UpdateExpression="SET #s = :next, #ts = :now",
            ConditionExpression="#s = :expected AND #v = :version",
            ExpressionAttributeNames={"#s": "status", "#v": "version", "#ts": timestamp_attribute},
            ExpressionAttributeValues={
                ":expected": expected_status, ":next": next_status,
                ":version": int(version), ":now": int(time.time()),
            },
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def lambda_handler(event, context):
    action = event.get("action", "check")
    if action == "check":
        result = TABLE.get_item(Key={"pk": DEVICE_KEY}, ConsistentRead=True)
        item = result.get("Item")
        if not item or item.get("status") != "ARMED":
            return {"started": False, "reason": "Not armed"}
        if int(item["expires_at"]) > int(time.time()):
            return {"started": False, "reason": "Lease still active"}

        version = int(item["version"])
        # Standard workflows cannot start a second execution with the same name.
        # Together with the atomic 'claim' in the first state, this handles duplicate ticks.
        execution_name = f"{DEVICE_KEY}-v{version}"
        try:
            STEP_FUNCTIONS.start_execution(
                stateMachineArn=MACHINE_ARN, name=execution_name,
                input=json.dumps({"version": version}),
            )
            return {"started": True, "execution_name": execution_name}
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ExecutionAlreadyExists":
                return {"started": False, "reason": "Already started"}
            raise

    version = int(event["version"])
    if action == "claim":
        # A heartbeat can win the race even after a tick observed expiration.
        try:
            TABLE.update_item(
                Key={"pk": DEVICE_KEY},
                UpdateExpression="SET #s = :cycling, claimed_at = :now",
                ConditionExpression="#s = :armed AND #v = :version AND expires_at <= :now",
                ExpressionAttributeNames={"#s": "status", "#v": "version"},
                ExpressionAttributeValues={
                    ":cycling": "CYCLING", ":armed": "ARMED",
                    ":version": version, ":now": int(time.time()),
                },
            )
            return {"claimed": True}
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return {"claimed": False}
            raise

    if action == "finish":
        return {"finished": _change_status(version, "CYCLING", "TRIPPED", "last_cycled_at")}

    if action == "fail":
        return {"failed": _change_status(version, "CYCLING", "ERROR", "last_error_at")}

    raise ValueError(f"Unsupported action: {action}")
