# FolioEx

**Your portfolio, explained.** FolioEx is a local, single-user app that turns your brokerage *Transaction
History* CSV files into an interactive, plain-English view of your U.S. stock portfolio: what you own, how it
has done over time (dividends included), how it compares with the market, and how it stacks up against stocks
you're curious about.

> **Disclaimer.** FolioEx is for information and education only. It is not investment, tax, or legal advice.
> Past performance does not guarantee future results. Market data comes from Yahoo Finance and may be delayed,
> incomplete, or wrong; always confirm against your broker statements. Gains, dividends, and withholding figures
> are estimates and are not for tax filing. For decisions about your money, talk to a licensed financial adviser.
> Your files are processed on your computer and are not saved when you close FolioEx.

## How to run

**1. Install uv (once).** FolioEx is a [uv](https://docs.astral.sh/uv/) project, and uv installs the right Python
(3.12 or newer) for you. Check it is available with `uv --version`.

**2. Set up (once, from the project folder).**

```
uv sync
```

This creates a local `.venv` folder and installs everything FolioEx needs.

**3. Start FolioEx.**

```
uv run folioex
```

The terminal prints the address and the log file location, and your browser opens at
<http://127.0.0.1:8050>. Keep the terminal open while you use the app.

**4. Try it.** No files yet? On the **Data Hub** tab press **Load sample data** to explore with six years of
made-up transactions, or **Download template** and fill it in with your own.

**5. Stop it.** Press `Ctrl+C` in the terminal. Your uploaded data disappears with it; nothing personal is saved.

An internet connection is used for market prices (Yahoo Finance) and the stock list on the Compare tab. Without
one, FolioEx shows the prices it saved earlier, with a note saying so.

### Run options

Any setting in [config.toml](config.toml) can be changed for a single run with an environment variable (prefix
`FOLIOEX_`, and `__` between levels):

| I want to… | Windows PowerShell | macOS / Linux / Git Bash |
|---|---|---|
| use another port | `$env:FOLIOEX_UI__PORT = "9000"; uv run folioex` | `FOLIOEX_UI__PORT=9000 uv run folioex` |
| not open the browser | `$env:FOLIOEX_UI__OPEN_BROWSER = "false"; uv run folioex` | `FOLIOEX_UI__OPEN_BROWSER=false uv run folioex` |
| start in dark theme | `$env:FOLIOEX_UI__DEFAULT_THEME = "dark"; uv run folioex` | `FOLIOEX_UI__DEFAULT_THEME=dark uv run folioex` |

On Windows PowerShell the variable stays set for that terminal window; clear it with
`Remove-Item Env:FOLIOEX_UI__PORT`. To make a change permanent, edit `config.toml` instead.

### If something doesn't work

- **The page doesn't open:** copy the address printed in the terminal into your browser.
- **"Address already in use":** another program (or an earlier FolioEx) is on that port. Stop it, or use another port
  as shown above.
- **A message appears on screen:** FolioEx explains problems in plain English. The technical details are in
  `~/.folioex/logs/folioex.log`.
- **Prices look old or are missing:** you are probably offline or Yahoo Finance is not answering. Check your
  connection and reload; saved prices are used in the meantime.

## What's in it

| Tab | What it shows |
|---|---|
| **Data Hub** | Upload up to 20 CSV files (5 MB each, 25 MB total). A 25-candle progress bar runs while your numbers are built, then a short *guess your return* quiz. A report lists every file, years covered, and anything worth checking, in plain English. Download the blank template here. |
| **Holdings** | Account value, total gain on deposits, cash, today's change, dividends. A treemap of where your money is, sector and company-size splits, what drove your gains, and a table of every holding. Your 3 best and 3 weakest holdings since you bought them. |
| **Performance** | Pick a period (1M … All, or custom dates) and a benchmark. Your return vs the market on the same deposits, best and weakest stocks in the period (sold ones included), month-by-month returns, where your gains came from, and a full **dividend income** section (by month, year, and stock). |
| **Compare** | Pick stocks you own and stocks from your wishlist (any NYSE or Nasdaq stock or ETF, or type any ticker). Up to 8 at once, plus your whole portfolio. Growth of $100, and a side-by-side table (return, yearly return, ups and downs, biggest drop, Sharpe ratio, dividend yield, P/E, market cap) with a ★ on the best in each column. |

Every tab has a **💡 Explain** button: a short summary of that screen, written from your own numbers.
There is a light/dark toggle, and the **Compare against** picker (S&P 500, Nasdaq-100, Dow Jones 30) is shared by
Performance and Compare.

## Your data: the CSV format

Press **Download template** in the Data Hub for a blank file with placeholders and a column guide. The file is a
*sectioned* CSV: the first column names the section, the second says `Header` or `Data`. Only the
`Transaction History` section is required.

| Column | Meaning |
|---|---|
| Date | `DD/MM/YYYY`, e.g. `31/12/2026` |
| Account | free text |
| Transaction Type | `Buy`, `Sell`, `Dividend`, `Deposit`, `Withdrawal`, `Foreign Tax Withholding`, `Other Fee`, `Credit Interest`, `Transfer In`, `Stock Split` … |
| Symbol | ticker, e.g. `BRK B` or `BRK.B` (both become `BRK-B`) |
| Quantity | shares; the sign doesn't matter, the transaction type decides the direction |
| Price, Price Currency | `USD` only (other currencies are skipped and reported) |
| Gross Amount, Commission, Net Amount | `Net = Gross + Commission`; buys are negative, sells and income positive |

Good to know:

- Use `-` for an empty cell. Rows may be in any order, and several files may overlap: identical rows across
  files are merged, identical rows inside one file are all kept (partial fills).
- An unknown transaction type still moves cash; FolioEx tells you which label it didn't recognise. Add a
  mapping under `[format.types]` in `config.toml` to teach it.
- Problems never stop the whole upload: a bad row is skipped, a bad file is skipped, and each is explained.
- A `Summary` section (starting and ending cash) lets FolioEx double-check your cash. Starting cash counts as an
  opening deposit.

## Configuration

All settings live in [config.toml](config.toml); every value shown there is the default. Any setting can be
overridden by an environment variable with the `FOLIOEX_` prefix and `__` between levels, e.g.
`FOLIOEX_UI__PORT=9000` or `FOLIOEX_QUIZ__ENABLED=false`.

| Section | What you can change |
|---|---|
| `[upload]` | file count and size limits |
| `[benchmark]` | default benchmark and the choices offered |
| `[analytics]` | risk-free rate (for the Sharpe ratio) |
| `[insights]` | thresholds for the plain-English notes (concentration, big winners and losers, idle cash, fee drag) |
| `[cache]` | where public market data is kept, and for how long |
| `[compare]` | how many picks (max 8), default period, whole-portfolio line on or off |
| `[quiz]` | switch the guess-your-return quiz off, or change the range width |
| `[performers]` | how many best and weakest stocks to show, and minimum holding time and size |
| `[format]` | date format, column names, transaction-type mapping, ticker overrides |
| `[sample]` | tickers, years and seed for regenerating the sample data |
| `[ui]` | name, tagline, theme, host, port |
| `[logging]` | log folder and level |

Wording of every message is in `src/folioex/messages.toml`; the Explain panel's wording is in
`src/folioex/explain.toml`.

## Where things are stored

| What | Where |
|---|---|
| Your transactions | **Nowhere.** They live in memory until you close FolioEx. |
| Public market data (prices, dividends, company details, stock list) | `~/.folioex/cache` |
| Technical log (for troubleshooting; no amounts) | `~/.folioex/logs/folioex.log` |

You never see technical errors on screen. If something goes wrong you get a plain-English message, and the
details go to the log file.

## Security and privacy

FolioEx is built for one person on one computer. Please keep it that way:

- **Keep it on your own machine.** By default it only answers on `127.0.0.1`, so nobody else on your network can
  reach it. FolioEx has **no login**. If you change `host` in `config.toml` (for example to `0.0.0.0`), anyone who
  can reach that address can see whatever data is loaded. Don't do that on a shared or public network.
- **It uses Dash's built-in development web server**, which is fine for local use but not meant to be put on the
  internet.
- **Your transactions stay in memory.** They are never written to disk and are gone when you stop the app or
  press **Clear data**. Only the latest upload is kept.
- **What leaves your computer.** To fetch prices and company details, FolioEx sends the *ticker symbols* you hold
  or compare to Yahoo Finance, and downloads the NYSE/Nasdaq stock list from Nasdaq Trader. The price
  requests also carry the date range your history covers. No amounts or file contents are sent. The page also loads its fonts from Google Fonts, so Google can see that you opened it.
- **What is saved on disk.** `~/.folioex/cache` holds public prices and company details. The file names reveal
  which tickers you looked up, so treat that folder as personal. The log (`~/.folioex/logs/folioex.log`) can
  contain file names, row numbers and ticker symbols, but never amounts. Delete either folder whenever you like;
  FolioEx rebuilds them.
- **Your CSV files.** Keep your real transaction files out of any folder you share or publish (for example a
  public repository). Uploads are limited in count and size, and files are only read as text, never run.
- **Not for tax filing, and not advice.** See the disclaimer at the top.

## How the numbers are worked out

- **Total gain on deposits** = (account value − money you put in) ÷ money you put in. Account value is holdings
  plus cash.
- **Return (time-weighted)** ignores when you added money, so it is fair to compare with the market. **Yearly
  return (XIRR)** is what your actual money earned per year given your timing; both are shown, yearly figures only
  for periods of a year or more.
- **Benchmark**: "what if every deposit had gone into the index on the same day?"
- **Cost basis** is FIFO and includes commissions. Splits are applied once, from your file or from market data.
- **Dividends** come only from your files (never double-counted from Yahoo Finance).

## Regenerating the sample data

The bundled files in `src/folioex/sample/files/` were generated from real market data (made-up deposits and
trades). To rebuild them (needs the internet):

```
uv run python scripts/generate_sample_data.py
```

## Development

```
uv run python scripts/check.py          # lint, format, types, tests, coverage
uv run playwright install chromium     # once, for the browser tests
uv run python scripts/check.py --e2e   # the same, plus the browser tests
uv run pytest --update-golden tests/unit/test_golden.py   # only after a deliberate change to the numbers
```

A change is done when every gate passes: `ruff check`, `ruff format --check`, `mypy --strict`, the tests with the
network switched off, and coverage (90% on the core logic, 70% overall).

Layout of `src/folioex/`:

| Folder | Role |
|---|---|
| `domain/`, `ingestion/` | the transaction model; CSV reading, merging, checks, and the template |
| `market_data/` | Yahoo Finance behind an on-disk cache; the NYSE and Nasdaq stock list |
| `analytics/` | positions, valuation, returns, income, allocation, comparison, insights (pure pandas, no UI) |
| `jobs/` | the background run with its 25 named stages |
| `ui/` | Dash app: layout, one module per tab, shared components, charts, theme |

The analytics and ingestion layers never import the UI (a test enforces it), and no ticker symbol is hard-coded
outside `config.toml`.

## Limits

Single user, local only. U.S. stocks and ETFs in USD. Spin-offs and mergers aren't modelled (they show up as
unrecognised rows with a message). A renamed ticker needs an entry under `[format.symbols]`. Yahoo Finance is an
unofficial source and can rate-limit or change without notice; FolioEx falls back to saved prices when it can.
