# aust-hefei-campus-login

Captive-portal authentication for the AUST Hefei campus network, reduced to a single action.

![Screenshot](screenshots/main.png)

The portal runs Dr.COM eportal. This does not drive a browser through the login form. It
issues the portal's own authentication request directly, which takes about a second
instead of tens of seconds. Credentials are supplied once and held locally under Windows
DPAPI.

## Status

Beta. The implementation targets a single portal deployment and has been validated only
against it. Portal software is upgraded without notice and its interface is not
contractual. Assume it can break; `login.log` records each exchange verbatim, which is
usually enough to identify why.

## Scope

Hefei campus — portal `172.24.34.2`, student mobile egress `@hfcmcc`.

The Huainan campus is a separate deployment: different portal address, different egress
table, no route between the two. Nothing here transfers to it.

## Operation

Authentication is one GET:

```
GET http://172.24.34.2:801/eportal/portal/login
      ?callback=dr1003
      &login_method=1
      &user_account=,0,<student-id><egress>
      &user_password=<password>
      &wlan_user_ip=<local campus address>
      &wlan_user_ipv6=
      &wlan_user_mac=000000000000
      &wlan_ac_ip=
      &wlan_ac_name=
      &jsVersion=4.2
      &lang=zh
```

`,0,` prefixes a desktop client; mobile clients send `,1,`. The reply is JSONP —
`dr1003({"result":1,...})`. A `result` of `1` or `ok` establishes a session; `ret_code: 2`
reports that the address was already online and is handled identically.

Every parameter comes from the portal's own client code, not from inspection of traffic.
`a41.js` supplies the endpoint and the terminal probe; `a40.js` contains
`login.login_portal()`, where the request is assembled; the active scheme page
`extern/<program_index>/<page_index>/pc.js` carries the egress table:

```html
<select name="ISP_select">
  <option value="-1">Select egress</option>
  <option value="">Campus resources only</option>
  <option value="@aust">China Telecom</option>
  <option value="@hfcmcc">China Mobile (Hefei)</option>
</select>
```

The host's own address is not configured but read at runtime from `drcom/chkstatus`, so a
new DHCP lease requires no change.

### Connectivity detection

`chkstatus` is not a usable indicator on this deployment — it returns `result: 0` from a
host that is demonstrably online. Connectivity is established instead over HTTPS with
ordinary certificate validation.

An unauthenticated portal can intercept HTTP and serve a login page; it cannot present a
valid certificate for a public hostname. A TLS failure is therefore a sound negative
signal where a status-code test is not. Hosts the campus whitelists before authentication
— `msftncsi.com`, `captive.apple.com`, `detectportal.firefox.com` and their kin — answer
in either state, and are excluded for that reason.

The consequence is that on a host which already has connectivity, the tool contacts the
portal not at all.

### Behaviour

- Authentication only. There is no logout path, and running against a live session is a
  no-op.
- Transient failures are retried. A password rejected on its trailing punctuation is
  retried with the alternate form — full-width `U+FF01` against half-width `U+0021`.
- The portal's response is logged unmodified. Nothing is transmitted anywhere except the
  campus portal and the connectivity probes.

## Building

```
pip install -r requirements.txt
python aust_campus.py
```

To produce a standalone executable:

```powershell
pip install pyinstaller customtkinter

pyinstaller --noconfirm --onedir --noconsole --name AUSTHFCampus `
    --collect-all customtkinter `
    --exclude-module numpy --exclude-module scipy --exclude-module pandas `
    aust_campus.py
```

The `--exclude-module` flags are load-bearing. Pillow declares numpy as an optional
dependency; when numpy is present, PyInstaller follows it and bundles the accompanying
OpenBLAS, turning a 15 MB build into a 73 MB one to no purpose.

### Command line

| Flag | Effect |
| --- | --- |
| *(none)* | Open the window |
| `--silent` | Authenticate without a window; suited to a scheduled task |
| `--check` | Report network state and exit, issuing no authentication request |
| `--dry-run` | Print the request that would be sent, without sending it |
| `--force` | Re-authenticate against a live session |

## Porting to another deployment

The method generalises to any Dr.COM eportal deployment.
[tools/probe_portal.py](tools/probe_portal.py) performs the reconnaissance, reading
`chkstatus` and `loadConfig` and reporting the scheme identifiers, authentication method
and suffix handling. It issues no authentication request.

```bash
python tools/probe_portal.py <portal-host> [port]
```

The scheme page named in its output carries the egress table; the `value` attributes are
the suffixes. `PORTAL_HOST`, `PORTAL_PORT` and `CHANNELS` at the head of `aust_campus.py`
are the only values that require changing.

Deployments predating eportal v4 — this university's Huainan campus among them —
authenticate by form POST to `/a79.htm` or GET to `/drcom/login`. This project does not
address them.

## Files

| Path | Contents |
| --- | --- |
| `config.json` | Account and settings, under `%APPDATA%\CampusNetLogin`. The password field holds a DPAPI blob, readable only by the Windows account that wrote it; with "remember password" unchecked it is never written at all. |
| `login.log` | Timestamped record of every action, in the same directory. |

The interface is in Chinese. The intended users read Chinese.

## License

[MIT](LICENSE)
