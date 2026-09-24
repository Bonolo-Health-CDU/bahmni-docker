# Bahmni Print Agent

This is a lightweight polling agent for the Bahmni/Odoo print queue.

It connects to Odoo over XML-RPC, fetches pending `bahmni.print.job` records,
dispatches each payload to the configured printer target, then writes the result
back to Odoo.

The current implementation is intentionally command-line first so the workflow
can be demonstrated before the Windows tray packaging is added.

## Printerless Demo

In Odoo, create or choose an API user and give it the **CDU Print Agent**
group. For a quick internal demo you can use an admin account, but production
should use a restricted print-agent account.

Use dry-run mode to prove the queue works without physical printers:

```bash
cd print-agent
cp config.example.ini config.local.ini
# edit config.local.ini with the Odoo URL, DB, username, and password/API key
python3 -m bahmni_print_agent --config config.local.ini --once
```

In dry-run mode, printed output is written under:

```text
print-agent/printed-output/
```

Expected result:

1. Confirm dispensing in Odoo.
2. Odoo creates pending `bahmni.print.job` rows.
3. Run the agent once.
4. Payload files appear in `printed-output`.
5. Odoo print jobs move from `Pending` to `Done`.

For continuous polling during a demo, omit `--once`:

```bash
python3 -m bahmni_print_agent --config config.local.ini
```

## Real Printer Setup Later

When the printer PCs are available:

- Set `dry_run = false`.
- Configure `product_printer` and `bagging_printer` as `raw_tcp` targets.
- Configure `document_printer` as a local print command or CUPS/IPP queue.

Example raw TCP printer:

```ini
[printer.product_printer]
type = raw_tcp
host = 192.168.1.50
port = 9100
```

Example local print command:

```ini
[printer.document_printer]
type = command
command = lp -d HP_LaserJet {file}
```

The `{file}` placeholder is replaced with a temporary payload file path.
