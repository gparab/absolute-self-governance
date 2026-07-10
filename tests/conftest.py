import os

os.environ["TESTING"] = "True"
os.environ["ALLOW_GUEST_ACCESS"] = "true"
# Webhook signature verification is never bypassed, even under TESTING;
# tests sign their payloads with this secret instead.
os.environ["WEBHOOK_SECRET"] = "test-webhook-secret"
