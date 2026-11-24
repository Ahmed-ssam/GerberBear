# Privacy

Gerber Bear runs on your computer. It has no account, no telemetry and no server of its own.

| What | Where it goes | When |
|---|---|---|
| Your chat messages and the design requirements | The AI provider you configured (Google Gemini, OpenAI, or your own local server), using **your** key, under that provider's terms | Each chat turn |
| Component values and packages (for example "10 kΩ 0603") | The public JLCPCB parts index at jlcsearch.tscircuit.com, to find supplier part numbers | When an order package is built |
| A request for the latest version number | api.github.com (this project's releases) | Once when the app starts |

- **API keys** are stored only in Windows Credential Manager, are never written to files or logs,
  and are sent only to the provider they belong to.
- **Your designs** (schematics, boards, files) stay in your data folder
  (`%APPDATA%\AIPCBDesigner`) unless you export or share them.
- The **diagnostic report** (... -> Save diagnostic report) is a zip you create and attach yourself;
  it contains the app log (keys redacted), system details and the current run record.
- Freerouting is started with its telemetry and analytics switched off.
