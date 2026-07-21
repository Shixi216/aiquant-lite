# CITIC Securities QMT / xtquant adapter reservation

The repository records CITIC Securities as the intended future broker integration, but the user's
QMT, Python API, and simulation entitlements are unconfirmed. The only active execution adapter is
`paper`. The `citic_qmt_xtquant` adapter is a non-connecting placeholder: it does not import
xtquant, inspect the local QMT installation, accept account identifiers, or store credentials.

The planned contract follows the official XtQuant lifecycle: connect and subscribe, query assets,
positions, orders and trades, submit and cancel orders, process callbacks, then reconnect and
reconcile after failures. See the official [XtQuant trading API](https://dict.thinktrader.net/nativeApi/xttrader.html),
[quick start](https://dict.thinktrader.net/nativeApi/start_now.html), and
[FAQ](https://dict.thinktrader.net/nativeApi/question_function.html?id=TB5IbM).

Before implementation, the broker must confirm QMT availability, Python trading entitlement, an
isolated simulation environment, supported client/version, connection restrictions, order types,
and official operational limits. No password, trading password, Cookie, token, key, QR screenshot,
or client login file may be added to this repository or supplied to an Agent.
