# Bahmni Print Agent

This is a lightweight polling agent for the Bahmni/Odoo print queue.

It connects to Odoo over XML-RPC, fetches pending `bahmni.print.job` records,
dispatches each payload to the configured printer target, then writes the result
back to Odoo.

The same code can run as a command-line worker, a local status UI, or a Windows
tray application.

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

Check Odoo and printer reachability without processing jobs:

```bash
python3 -m bahmni_print_agent --config config.local.ini --status
```

Check only the configured printers:

```bash
python3 -m bahmni_print_agent --config config.local.ini --test-printers
```

Open the local status UI without a tray icon:

```bash
python3 -m bahmni_print_agent --config config.local.ini --status-ui
```

Run the tray app:

```bash
python3 -m bahmni_print_agent --config config.local.ini --tray
```

The status UI is served on localhost. It shows Odoo connectivity, printer
connectivity, active/standby role, visible peers, recent jobs, retry buttons for
failures, and test-print actions for each configured printer.

## Remote CDU Deployment

For the hosted-server setup, start from:

```bash
cp config.remote.example.ini config.local.ini
```

Then edit `config.local.ini` on each CDU PC:

- Set `[odoo] url` to the hosted Odoo/Bahmni URL.
- Use the restricted print-agent API user.
- Keep `dry_run = false`.
- Confirm the included local printer IPs: `product_printer` = `192.168.26.43`,
  `bagging_printer` = `192.168.26.40`, and `box_label_printer` =
  `192.168.26.41`.
- Confirm the Windows printer names for receipt/document queues:
  `CDU_RECEIPT` and `CDU_DOCUMENT`.
- Set each PC's election `priority`; lower values become active first.
- Keep `[ui] host = 127.0.0.1` unless the status page must be exposed locally.

Only the local agent talks to printers. The hosted server only receives outbound
XML-RPC calls from the agent and never needs access to the CDU printer network.

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

The command can also use `{config_dir}`, which is replaced with the directory
containing the active INI file. The remote example uses this for the Windows PDF
helper:

```ini
command = powershell -ExecutionPolicy Bypass -File {config_dir}/scripts/print_pdf_windows.ps1 -PrinterName "HP LaserJet" -File {file}
```

That helper uses SumatraPDF for silent PDF printing. Install SumatraPDF on the
printer PC or set the `CDU_SUMATRA_PDF` environment variable to
`SumatraPDF.exe`.

## Box Label and Manifest Routing

Printing box documents creates two separate jobs:

| Document | Format | Logical printer | Hardware |
| --- | --- | --- | --- |
| Box label | ZPL | `box_label_printer` | ZPL label printer |
| Manifest | A4 PDF | `document_printer` | A4 document printer |

Check the actual printer at each configured IP. Sending the box label's ZPL
commands to an HP document printer can produce a sheet containing `^XA`, `^PW812`,
and `^FO` instead of a label. A successful TCP connection does not establish that
the printer understands ZPL.

On Linux, `document_printer` must name the document printer's CUPS queue. Inspect
its device URI and model with `lpstat -v QUEUE_NAME` and `lpoptions -p QUEUE_NAME`.
For example, if the HP printer is installed as `CDU_HP_M227`:

```ini
[printer.document_printer]
type = command
queue = CDU_HP_M227
test_language = pdf
```

After installing the manifest fix, upgrade the Odoo `cdu_elmis` module and restart
the print agent. The upgrade repairs the bundled manifest template if it was
configured as a label or routed to another logical printer. The agent rejects
non-PDF document jobs and invalid PDF signatures. Use **Print Box Documents**
again to generate fresh jobs after correcting the configuration; retrying an old
job retains its original format and payload.

## Collect-N-Go Barcodes

The box and bag Code128 barcodes use the same slash-free `ExternalReference`
sent to Collect-N-Go. For example, `CDU/BOX/2026/00027` is encoded as
`CDUBOX202600027`. PDF labels and the manifest use the same barcode values as
the ZPL labels.

The bag's tracking barcode identifies its parcel. Odoo reserves that parcel
reference before printing, and uses it again after QA and during dispatch.
Scan this barcode in Collect-N-Go or when adding the bag to a box. The separate
bag QR code carries the patient name and prescription reference for QA.

After upgrading `cdu_elmis`, reprint affected labels to obtain the corrected
barcodes. Existing Odoo record names and previously submitted Collect-N-Go
references are retained.

## Active/Standby Election

When running the agent on multiple CDU PCs, enable the peer election block on
each PC. Only the elected active agent polls Odoo; standby agents keep running
and take over when the active agent has not been seen for about six seconds.

```ini
[election]
enabled = true
priority = 100
port = 47990
broadcast_interval_seconds = 2
peer_timeout_seconds = 6
broadcast_address = 255.255.255.255
```

Use a lower `priority` value for the preferred active PC. If priorities match,
the hostname decides the active instance. For troubleshooting, start one agent
with `--force-active`.

## Windows Build

From a Windows machine with Python installed:

```powershell
cd print-agent
powershell -ExecutionPolicy Bypass -File scripts/build_windows_agent.ps1
```

The executable is written to:

```text
dist\BahmniPrintAgent.exe
```

Copy `config.remote.example.ini` to `config.local.ini` beside the executable and
edit it for that PC. To run at login:

```powershell
.\BahmniPrintAgent.exe --config config.local.ini --install-startup
```

To remove login startup:

```powershell
.\BahmniPrintAgent.exe --uninstall-startup
```
