# Altera firesim port Kasa KP100 AWS watchdog

One fixed Amazon EventBridge Scheduler tick per minute. Each *successful* heartbeat grants a fresh four-hour lease in DynamoDB. If it expires, AWS Step Functions performs one cloud power cycle: OFF → wait 60 seconds → ON. The state switches to `TRIPPED`, so it does not power cycle repeatedly until another heartbeat rearms it.

## Deploy

Prerequisites: AWS CLI with permissions to deploy SAM/CloudFormation, AWS SAM CLI, and a TP-Link account with working remote control. The currently tested `tplink-cloud-api` path does not handle this account's MFA challenge; avoid permanently disabling MFA if possible. Deploy a regional cloud function without a VPC attachment, so it can access TP-Link's Internet endpoints.

1. In AWS Secrets Manager, create an **Other type of secret** (plaintext JSON) with:

   `{"username":"YOUR_TPLINK_EMAIL","password":"YOUR_TPLINK_PASSWORD"}`

   Copy the secret's full ARN. Do not commit credentials to source control or place them in Lambda environment variables.
2. Determine the *exact alias* of your discovered HS105(US)/KP100 from `tplink-cloud-api`.
3. Deploy:

   ```bash
   sam build
   sam deploy --guided
   ```

   Select the AWS Region; for parameters enter the secret ARN, Kasa device alias, optionally AlarmEmail. Acknowledge IAM role creation. Confirm your SNS email subscription if you specified one.
4. Retrieve the heartbeat Lambda name:

   ```bash
   aws cloudformation describe-stacks --stack-name YOUR_STACK_NAME --query 'Stacks[0].Outputs' --output table
   ```
5. Arm or renew the watchdog:

   ```bash
   aws lambda invoke --function-name YOUR_HEARTBEAT_FUNCTION --cli-binary-format raw-in-base64-out --payload '{}' /dev/stdout
   ```

   **Important:** Lambda invocation returning HTTP `StatusCode: 200` is not proof that the lease renewed. Check `accepted: true` in the returned JSON; `accepted: false` means it was rejected because a cycle is underway or failed.

   Use an authenticated AWS principal with `lambda:InvokeFunction` scoped ONLY to the heartbeat function ARN for external calls. The heartbeat Lambda requires no HTTP exposure. For example, Altera firesim port can call this with boto3 from Google Cloud if you give it limited AWS credentials, or better use AWS role federation/OIDC.

## Behavior and recovery

- The `expires_at` field is an application **lease deadline**, not DynamoDB's automatically deleted TTL attribute. DynamoDB TTL deletion is not suitable for precise scheduling.
- EventBridge Scheduler checks once a minute. Its one-minute granularity and Lambda delays mean expiry is **not precise to the second**.
- A delayed stale check cannot run an old cycle if a newer heartbeat has already renewed the lease: the Step Functions `ClaimLease` state makes an atomic conditional write against `version`, `status` and `expires_at`.
- The cycle is only triggered **once per lease**; it ends in `TRIPPED`. The next heartbeat re-arms it. A heartbeat arriving during `CYCLING` is rejected.
- If `OFF` reports failure, the workflow *still tries ON after 60 seconds* because an OFF operation may have succeeded just before its reply was lost. After turning ON, it records `ERROR` and fails the execution so the alarm fires.
- If ON repeatedly fails, the workflow records `ERROR` and raises a CloudWatch alarm on the Step Functions execution failure. In `ERROR`, heartbeats are rejected until an operator checks the physical power state and resets the item. No software-only method guarantees recovery from a smart plug with broken connectivity or unavailable cloud service.
- Manual recovery after checking the hardware: use DynamoDB Console to set the item `status` from `ERROR` to `TRIPPED`, then send a fresh heartbeat. Do not manually alter `version` or `expires_at` during a running cycle.
- DynamoDB `LeaseTable` is retained after deleting the CloudFormation stack to avoid accidentally destroying state.
- AWS Step Functions Standard and Kasa cloud operations can be delivered or retried more than once; power **on** and **off** are idempotent commands, but power interruption always carries risks for disks and compute nodes. Arrange a graceful shutdown before the fallback deadline if possible.

## Quick verification

Inspect DynamoDB item, then exercise a deliberately short deadline **only in a disposable test environment**, not with a live cluster:

- First invoke the heartbeat function and confirm `accepted: true`.
- Confirm DynamoDB item shows `ARMED` and `expires_at` around four hours from now.
- To do an expiry test, use DynamoDB Console to set `expires_at` to a Unix timestamp 2 minutes in the future. Do this on a dedicated test plug only. Within ~1 minute after expiration, the workflow should start.
- In Step Functions, verify `ClaimLease → TurnOff → WaitOneMinute → TurnOn → MarkFinished` and the final DynamoDB status `TRIPPED`.

## Limitations

- One deployment controls one plug alias. Use a separate deployment per independent plug or extend the application with per-device configuration.
- While checking every minute costs little at low scale, it does generate about 1,440 check invocations per day per stack.
- Losing Google Cloud connectivity does not stop AWS's watchdog, **but** the plug and TP-Link's cloud need electricity and Internet access. The service relies on unofficial `tplink-cloud-api`, which TP-Link could change.
