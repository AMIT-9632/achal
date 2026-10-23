# ACHAL alerts service

Turns each `achal/{site}/alert` into messages for the right people, in their
language, and keeps escalating until an operator acknowledges.

- **Recipients**: `config/recipients/<site>.example.yaml` (fictional people, dummy numbers). Real contacts go in
  `config/recipients/<site>.local.yaml` (gitignored), selected with `RECIPIENTS_FILE`.
  Each recipient has a language, channels (SMS, email), the lowest level they want, an escalation tier, and
  `public: true` for community members (plain message without node ids).
- **Languages**: `src/templates/messages.json`. Santali falls back to Hindi until a native speaker writes and
  reviews it (`docs/translations.md`). SMS length is computed per message (Indic text is UCS-2, 70 chars per part).
- **Rate limiting**: per recipient and channel, `per_hour` cap and a same-level cooldown. The cap never blocks a
  critical alert or a change of level.
- **Escalation**: tier 0 at once; tier 1 after `escalation_min[level]` minutes without acknowledgement, tier 2 after
  twice that. Acknowledgements arrive on `achal/{site}/alert/ack` (posted by the dashboard via the backend).
  The all-clear goes to everyone who was told.
- **Delivery log**: SQLite at `DELIVERY_LOG` (default `/data/deliveries.sqlite`), one row per attempt with status
  `sent`, `failed`, `suppressed_duplicate`, `suppressed_rate_limit`. A copy without contact details is published on
  `achal/{site}/alert/delivery` for the dashboard.
- **Channels**: `SMS_PROVIDER=twilio|fast2sms|mock|console`, SMTP for email. With no SMS credentials the **mock
  gateway** is used: it behaves like a provider (message id, GSM-7/UCS-2 part count), appends every SMS to
  `MOCK_SMS_OUTBOX` (default `/data/sms-outbox.jsonl`) and logs it as `sent_mock`, so the demo runs offline and is
  honest about it. Email without `SMTP_HOST` is logged to the console.

## Test alerts for the demo video

Without any account (mock gateway), one message per language plus phone-screen mock-ups:

```bash
cd services/alerts/src
python alerts.py --test-send --to +919800000000 --lang all   # writes sms-outbox.jsonl
python sms_preview.py                                         # writes sms-preview.html (labelled "simulated")
```

With a real SMS account, the same command sends to a real phone:

```bash
export SMS_PROVIDER=fast2sms FAST2SMS_API_KEY=...       # or twilio: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM
python alerts.py --test-send --to +9198XXXXXXXX --lang hi --level critical
```

Indian A2P SMS at scale needs a DLT-registered sender ID and templates (TRAI). Quick-route and international
gateways are fine for a one-off test; a pilot would register ACHAL's templates on DLT.

## Next

Voice call / IVR for feature phones and non-readers, WhatsApp, cell-broadcast via the district's CAP gateway,
delivery receipts from the provider webhooks.
