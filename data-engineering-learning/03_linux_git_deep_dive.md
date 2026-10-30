# 03 — Linux & Git Deep Dive

> **Companion project:** `../customer-sales-lakehouse`
> **Audience:** a motivated beginner on **Windows** who wants to become job-ready.
> **What you get out of this:** confidence at a Unix shell and genuine fluency with Git — the two tools you will touch every single working day as a data engineer.

This guide is deliberately long. Terminal and Git skills are where beginners quietly lose weeks of productivity, and where interviews quietly filter people out. Work through it with a real shell open.

---

## Table of contents

1. [Why a data engineer needs the terminal](#1-why-a-data-engineer-needs-the-terminal)
2. [Getting a Unix shell on Windows](#2-getting-a-unix-shell-on-windows)
3. [The filesystem tree, and paths](#3-the-filesystem-tree-and-paths)
4. [The core commands](#4-the-core-commands)
5. [Pipes, redirection, and combining tools](#5-pipes-redirection-and-combining-tools)
6. [Permissions](#6-permissions)
7. [Windows equivalents reference](#7-windows-equivalents-reference)
8. [Git: the mental model](#8-git-the-mental-model)
9. [Git: the core commands in practice](#9-git-the-core-commands-in-practice)
10. [Branching, merging, rebasing](#10-branching-merging-rebasing)
11. [A worked merge conflict](#11-a-worked-merge-conflict)
12. [The standard workflow for this project](#12-the-standard-workflow-for-this-project)
13. [Commit message conventions](#13-commit-message-conventions)
14. [.gitignore](#14-gitignore)
15. [Dangerous commands and how to recover](#15-dangerous-commands-and-how-to-recover)
16. [How this maps to the project](#16-how-this-maps-to-the-project)
17. [Quick-reference cheat sheet](#17-quick-reference-cheat-sheet)
18. [Exercises](#18-exercises)

---

## 1. Why a data engineer needs the terminal

You may have learned Python in a notebook and SQL in a GUI. That's fine for learning. It is not how data platforms run.

**Everything your code runs on is Linux:**

| Where your code ends up | Operating system |
|---|---|
| Spark cluster workers (Databricks, EMR, Dataproc, Synapse Spark) | Linux |
| Docker containers | Linux |
| Kubernetes pods | Linux |
| CI runners (GitHub Actions `ubuntu-latest`, GitLab CI) | Linux |
| Airflow schedulers and workers | Linux |
| Cloud VMs for scheduled jobs | Linux (overwhelmingly) |

**Concrete situations where a GUI cannot help you:**

1. **A Spark job failed at 03:00.** You SSH into the driver node and `tail -n 200` the log. There is no GUI.
2. **A CSV landed malformed.** Before launching a Spark session that takes 40 seconds to start, you `head -5` the file and immediately see the delimiter changed.
3. **A 4 GB file won't open in Excel.** `wc -l` tells you it has 18 million lines in half a second.
4. **CI is red.** The only artefact is a log file, and you read it with `grep -n "ERROR"`.
5. **You need to find which of 200 files references a deprecated column.** `grep -rn "old_column" src/` answers in one second.
6. **A container is misbehaving.** `docker exec -it <id> bash` drops you into a shell with no desktop environment whatsoever.
7. **Permissions are wrong on a mounted volume.** `ls -l` and `chmod` are the fix.

**And the deeper reason:** the terminal is *composable* and *scriptable*. A GUI action you performed is gone the moment you close the window. A command you ran can be put in a script, committed to Git, reviewed by a colleague, and run identically by a machine at 03:00 forever. **Reproducibility is the core value of data engineering, and the terminal is how you get it.**

---

## 2. Getting a Unix shell on Windows

You are on Windows. You have three realistic options; use them in this order of preference.

### Option 1 — WSL 2 (recommended)

Windows Subsystem for Linux gives you a genuine Ubuntu kernel inside Windows. This is the closest thing to what actually runs in production.

```powershell
# Run in an elevated (Administrator) PowerShell
wsl --install -d Ubuntu
```

Reboot, set a Unix username and password, and then:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3 python3-pip python3-venv openjdk-17-jdk
```

Your Windows drives are mounted under `/mnt/`:

```bash
cd /mnt/c/Users/<your-windows-username>/Desktop/programs/Learn
```

> **Performance tip:** file I/O across the `/mnt/c` boundary is slow. For heavy Spark work, keep the repo inside the Linux filesystem (`~/projects/...`) and use VS Code's **WSL** extension to edit it.

### Option 2 — Git Bash

Installed automatically with Git for Windows. It is a lightweight MinGW environment that provides the common Unix tools (`ls`, `grep`, `cat`, `find`, `wc`, `head`, `tail`, `chmod`) without a Linux kernel.

- **Good for:** learning the commands in this guide, running Git, quick file inspection.
- **Weak at:** anything needing real Linux behaviour — `systemd`, Docker internals, true POSIX permissions, package managers.

Every example in this guide runs in Git Bash unless explicitly noted.

### Option 3 — PowerShell with aliases

PowerShell ships aliases like `ls`, `cat`, `cp`, `mv`, `rm` that map to Windows cmdlets. **This is a trap for learners.** The aliases accept *different flags*:

```powershell
ls -l       # ERROR in PowerShell: 'l' is not a recognised parameter
cat -n file # ERROR: Get-Content has no -n
grep "x" f  # ERROR: grep doesn't exist at all
```

Use PowerShell for Windows administration. Use WSL or Git Bash to learn Unix. See §7 for a full translation table.

### Verify your setup

```bash
uname -a
git --version
python3 --version
```

Expected output (WSL Ubuntu):

```
Linux DESKTOP-ABC123 5.15.153.1-microsoft-standard-WSL2 #1 SMP ... x86_64 GNU/Linux
git version 2.43.0
Python 3.11.9
```

---

## 3. The filesystem tree, and paths

### 3.1 The tree

Unix has **one** tree rooted at `/`. There are no drive letters.

```
/
├── bin/          essential user binaries (ls, cp, cat)
├── etc/          system-wide configuration files
├── home/         user home directories
│   └── himanshu/ your home; abbreviated as ~
├── mnt/          mounted filesystems (WSL puts Windows drives here)
│   └── c/
├── opt/          optional / third-party software
├── tmp/          temporary files, wiped on reboot
├── usr/
│   ├── bin/      most user programs
│   └── local/    locally installed software
└── var/
    └── log/      log files live here
```

Windows, by contrast, has one tree *per drive*: `C:\`, `D:\`. In WSL, `C:\` appears as `/mnt/c`.

### 3.2 Our project tree

```
Learn/
├── data-engineering-learning/          <- you are here
│   ├── 00_data_engineering_roadmap.md
│   ├── 01_sql_deep_dive.md
│   ├── 02_data_modelling_deep_dive.md
│   ├── 03_linux_git_deep_dive.md
│   ├── 04_etl_elt_and_quality.md
│   ├── 08_dbt_orchestration_monitoring.md
│   ├── README.md
│   └── requirements.txt
└── customer-sales-lakehouse/           <- the companion project
    ├── config/
    │   ├── dev.yml
    │   └── prod.yml
    ├── data/
    │   ├── raw/
    │   │   ├── customers.csv
    │   │   ├── orders.csv
    │   │   └── products.csv
    │   ├── bronze/
    │   ├── silver/
    │   └── gold/
    ├── docs/
    ├── notebooks/
    │   ├── 01_bronze_ingestion.py
    │   ├── 02_bronze_validation.py
    │   ├── 03_silver_transformations.py
    │   ├── 04_silver_validation.py
    │   ├── 05_gold_dimensions.py
    │   ├── 06_gold_facts.py
    │   └── 07_data_quality_checks.py
    ├── orchestration/
    │   └── pipeline_design.md
    ├── sql/
    │   ├── 01_exploration_queries.sql
    │   ├── 02_data_quality_checks.sql
    │   ├── 03_reporting_queries.sql
    │   └── 04_star_schema_ddl.sql
    ├── src/
    │   ├── config.py
    │   ├── ingestion.py
    │   ├── logging_utils.py
    │   ├── quality_checks.py
    │   └── transformations.py
    └── tests/
```

### 3.3 Absolute vs relative paths

| Type | Starts with | Meaning | Example |
|------|-------------|---------|---------|
| **Absolute** | `/` | from the filesystem root; unambiguous anywhere | `/home/himanshu/Learn/customer-sales-lakehouse/data/raw/orders.csv` |
| **Relative** | anything else | from your *current* directory | `data/raw/orders.csv` |

### 3.4 The special path shortcuts

| Symbol | Meaning |
|--------|---------|
| `~` | your home directory (e.g. `/home/himanshu`) |
| `.` | the current directory |
| `..` | the parent directory |
| `-` | (with `cd`) the previous directory |
| `/` | the filesystem root |

```bash
# Standing in the learning repo
pwd
# /home/himanshu/Learn/data-engineering-learning

# '..' goes up one level; this is exactly how ../customer-sales-lakehouse works
ls ../customer-sales-lakehouse/data/raw
# customers.csv  orders.csv  products.csv

# '.' means "right here" — needed to run a script in the current directory
./run_pipeline.sh

# '~' always means home, regardless of where you are
cd ~
pwd
# /home/himanshu
```

### 3.5 Why relative paths matter for this project

The learning repo and the project are **siblings**. That is why every reference in these guides is `../customer-sales-lakehouse`, not an absolute path. Absolute paths break the moment someone clones the repo into a different folder, or CI checks it out at `/home/runner/work/...`.

> **Rule:** hardcode absolute paths in configuration (`config/dev.yml`), never in code. Code uses relative paths or values read from config.

### 3.6 Case sensitivity — a real trap on Windows

Linux filesystems are **case-sensitive**. `Orders.csv` and `orders.csv` are two different files. Windows is case-*insensitive*. Code that works on your laptop will fail in CI if you wrote `read_csv("Orders.csv")` and the file is actually `orders.csv`. Always match the case exactly.

---

## 4. The core commands

For every command: syntax, important flags, a realistic example against this project, and the expected output.

Assume you start here unless stated otherwise:

```bash
cd ~/Learn/data-engineering-learning
```

---

### 4.1 `pwd` — print working directory

**Syntax:** `pwd [-LP]`

| Flag | Meaning |
|------|---------|
| `-L` | logical path, following symlinks (default) |
| `-P` | physical path, resolving symlinks |

```bash
pwd
```

```
/home/himanshu/Learn/data-engineering-learning
```

**Why you need it:** almost every bug involving "file not found" is actually "I was in the wrong directory". `pwd` is the first thing to check, always.

---

### 4.2 `ls` — list directory contents

**Syntax:** `ls [options] [path...]`

| Flag | Meaning |
|------|---------|
| `-l` | long format: permissions, owner, size, modified time |
| `-a` | show hidden entries (names starting with `.`) |
| `-h` | human-readable sizes (with `-l`) |
| `-t` | sort by modification time, newest first |
| `-r` | reverse the sort order |
| `-S` | sort by size, largest first |
| `-R` | recurse into subdirectories |
| `-d` | list the directory itself, not its contents |
| `-1` | one entry per line |

```bash
ls
```

```
00_data_engineering_roadmap.md  02_data_modelling_deep_dive.md  04_etl_elt_and_quality.md           README.md
01_sql_deep_dive.md             03_linux_git_deep_dive.md       08_dbt_orchestration_monitoring.md  requirements.txt
```

Long listing, human-readable, newest first:

```bash
ls -lath
```

```
total 188K
-rw-r--r-- 1 himanshu himanshu  41K Oct  8 15:52 03_linux_git_deep_dive.md
-rw-r--r-- 1 himanshu himanshu  58K Oct  8 15:51 02_data_modelling_deep_dive.md
drwxr-xr-x 3 himanshu himanshu 4.0K Oct  8 15:50 .
-rw-r--r-- 1 himanshu himanshu  66K Oct  7 11:02 04_etl_elt_and_quality.md
-rw-r--r-- 1 himanshu himanshu  40K Oct  7 10:14 08_dbt_orchestration_monitoring.md
-rw-r--r-- 1 himanshu himanshu  38K Oct  6 09:31 00_data_engineering_roadmap.md
-rw-r--r-- 1 himanshu himanshu  33K Oct  6 09:30 01_sql_deep_dive.md
-rw-r--r-- 1 himanshu himanshu  13K Oct  6 09:12 README.md
-rw-r--r-- 1 himanshu himanshu 2.7K Oct  6 09:10 .gitignore
drwxr-xr-x 8 himanshu himanshu 4.0K Oct  6 09:05 ..
-rw-r--r-- 1 himanshu himanshu  278 Oct  6 09:05 requirements.txt
```

Note `-a` revealed `.gitignore`, `.` and `..`.

Listing the project's raw data:

```bash
ls -l ../customer-sales-lakehouse/data/raw
```

```
total 12
-rw-r--r-- 1 himanshu himanshu  162 Oct  6 09:20 customers.csv
-rw-r--r-- 1 himanshu himanshu  561 Oct  6 09:20 orders.csv
-rw-r--r-- 1 himanshu himanshu  171 Oct  6 09:20 products.csv
```

**Reading a long-listing line:**

```
-rw-r--r-- 1 himanshu himanshu 561 Oct  6 09:20 orders.csv
│└┬┘└┬┘└┬┘ │ └───┬──┘ └───┬──┘ └┬┘ └────┬───┘ └───┬───┘
│ │  │  │  │     │        │     │       │         └ name
│ │  │  │  │     │        │     │       └ last modified
│ │  │  │  │     │        │     └ size in bytes
│ │  │  │  │     │        └ group
│ │  │  │  │     └ owner
│ │  │  │  └ number of hard links
│ │  │  └ other permissions   (r--)
│ │  └ group permissions      (r--)
│ └ owner permissions         (rw-)
└ type: '-' file, 'd' directory, 'l' symlink
```

---

### 4.3 `cd` — change directory

**Syntax:** `cd [path]`

| Argument | Effect |
|---|---|
| *(none)* | go to `~` |
| `-` | go to the previous directory |
| `..` | go up one level |
| `~/x` | go to `x` inside home |

```bash
cd ../customer-sales-lakehouse
pwd
```

```
/home/himanshu/Learn/customer-sales-lakehouse
```

```bash
cd data/raw
pwd
```

```
/home/himanshu/Learn/customer-sales-lakehouse/data/raw
```

Jump back to where you were:

```bash
cd -
```

```
/home/himanshu/Learn/customer-sales-lakehouse
```

Go up two levels:

```bash
cd ../..
pwd
```

```
/home/himanshu/Learn
```

> **Tip:** press `Tab` to autocomplete paths. Press it twice to see all matches. This is not a nicety — it prevents typos that cost you minutes.

---

### 4.4 `mkdir` — make directory

**Syntax:** `mkdir [options] directory...`

| Flag | Meaning |
|------|---------|
| `-p` | create parent directories as needed; don't error if it already exists |
| `-v` | print what was created |
| `-m` | set permissions at creation time |

```bash
cd ~/Learn/customer-sales-lakehouse
mkdir -pv data/bronze/orders data/silver/orders data/gold/fact_sales
```

```
mkdir: created directory 'data/bronze'
mkdir: created directory 'data/bronze/orders'
mkdir: created directory 'data/silver'
mkdir: created directory 'data/silver/orders'
mkdir: created directory 'data/gold'
mkdir: created directory 'data/gold/fact_sales'
```

Create the whole medallion skeleton in one go using brace expansion:

```bash
mkdir -p data/{bronze,silver,gold}/{customers,products,orders}
ls data/bronze
```

```
customers  orders  products
```

> **`-p` is almost always what you want in scripts**, because it is idempotent: running it twice is harmless. Without `-p`, the second run fails with `File exists` and kills your script.

---

### 4.5 `cp` — copy

**Syntax:** `cp [options] source... destination`

| Flag | Meaning |
|------|---------|
| `-r` / `-R` | recursive — required for directories |
| `-i` | interactive — prompt before overwriting |
| `-n` | never overwrite |
| `-v` | verbose |
| `-p` | preserve mode, ownership, timestamps |
| `-a` | archive: `-rp` plus symlink preservation |
| `-u` | copy only if source is newer |

Back up the raw orders file before experimenting on it:

```bash
cd ~/Learn/customer-sales-lakehouse
cp -v data/raw/orders.csv data/raw/orders.csv.bak
```

```
'data/raw/orders.csv' -> 'data/raw/orders.csv.bak'
```

Copy all raw CSVs into a scratch folder:

```bash
mkdir -p scratch
cp -v data/raw/*.csv scratch/
```

```
'data/raw/customers.csv' -> 'scratch/customers.csv'
'data/raw/orders.csv' -> 'scratch/orders.csv'
'data/raw/products.csv' -> 'scratch/products.csv'
```

Copy a whole directory tree:

```bash
cp -rv data/raw scratch/raw_snapshot
ls scratch/raw_snapshot
```

```
customers.csv  orders.csv  products.csv
```

> **Trailing-slash gotcha:** `cp -r data/raw scratch/x` creates `scratch/x` as a copy of `raw`. If `scratch/x` already exists, it instead creates `scratch/x/raw`. When in doubt, check with `ls` afterwards.

---

### 4.6 `mv` — move or rename

**Syntax:** `mv [options] source... destination`

| Flag | Meaning |
|------|---------|
| `-i` | prompt before overwriting |
| `-n` | never overwrite |
| `-v` | verbose |
| `-u` | move only if source is newer |

`mv` does double duty: moving and renaming are the same operation.

```bash
# Rename
mv -v scratch/orders.csv scratch/orders_2026_02.csv
```

```
'scratch/orders.csv' -> 'scratch/orders_2026_02.csv'
```

```bash
# Move into a subdirectory
mkdir -p scratch/archive
mv -v scratch/orders_2026_02.csv scratch/archive/
```

```
'scratch/orders_2026_02.csv' -> 'scratch/archive/orders_2026_02.csv'
```

> **Danger:** `mv` overwrites the destination **silently** by default. `mv a.csv b.csv` destroys `b.csv` with no warning. Use `-i` interactively and `-n` in scripts until the behaviour is second nature.

---

### 4.7 `rm` — remove

**Syntax:** `rm [options] file...`

| Flag | Meaning |
|------|---------|
| `-r` | recursive — required for directories |
| `-f` | force — ignore missing files, never prompt |
| `-i` | prompt before every removal |
| `-I` | prompt once before removing more than 3 files |
| `-v` | verbose |
| `--` | end of options (protects files whose names begin with `-`) |

```bash
rm -v data/raw/orders.csv.bak
```

```
removed 'data/raw/orders.csv.bak'
```

```bash
rm -rv scratch
```

```
removed 'scratch/archive/orders_2026_02.csv'
removed directory 'scratch/archive'
removed 'scratch/customers.csv'
removed 'scratch/products.csv'
removed directory 'scratch'
```

### ⚠️ `rm` has no undo

There is **no recycle bin**. `rm` unlinks the file immediately and permanently.

```bash
rm -rf /            # catastrophic: attempts to erase the entire system
rm -rf ~            # erases your entire home directory
rm -rf $UNSET_VAR/* # expands to `rm -rf /*` if the variable is empty — a famous disaster
rm -rf .            # erases the current directory's contents
```

**Safety habits:**

1. **Always `ls` first.** Run `ls data/bronze/*` and confirm the list before running `rm -rf data/bronze/*`.
2. **Never combine `-r` and `-f` with a wildcard you haven't previewed.**
3. **Quote and brace your variables:** `rm -rf "${TARGET_DIR:?TARGET_DIR is unset}"/*`. The `:?` makes the shell abort if the variable is empty.
4. **Avoid trailing slashes after variables:** `rm -rf $DIR/` is the exact shape that caused real production outages.
5. **Prefer `trash-cli`** on a workstation: `sudo apt install trash-cli` then use `trash-put` instead of `rm`.

```bash
# Safe deletion pattern
TARGET="data/bronze/orders"
ls -la "$TARGET"            # 1. look
read -p "Delete? (y/N) " ok # 2. confirm
[ "$ok" = "y" ] && rm -rf "${TARGET:?}"  # 3. delete, guarded
```

---

### 4.8 `cat` — concatenate and print files

**Syntax:** `cat [options] file...`

| Flag | Meaning |
|------|---------|
| `-n` | number all output lines |
| `-b` | number non-blank lines only |
| `-A` | show all non-printing characters (tabs, line endings) |
| `-s` | squeeze repeated blank lines |

```bash
cd ~/Learn/customer-sales-lakehouse
cat data/raw/customers.csv
```

```
customer_id,customer_name,region,customer_created_date
1,Aisha Khan,North,2025-10-01
2,Ben Smith,South,2025-11-12
3,Carla Jones,North,2025-12-07
4,David Lee,West,2026-01-15
```

With line numbers:

```bash
cat -n data/raw/products.csv
```

```
     1	product_id,product_name,category,unit_price
     2	1001,Laptop Bag,Accessories,50.00
     3	1002,Wireless Mouse,Accessories,25.00
     4	1003,Monitor,Hardware,200.00
     5	1004,Keyboard,Accessories,75.00
```

Concatenate multiple files:

```bash
cat data/raw/customers.csv data/raw/products.csv > scratch/combined.txt
```

#### Diagnosing invisible characters

This is one of `cat`'s most valuable uses in data engineering. Windows line endings (`CRLF`) break Spark parsers in subtle ways:

```bash
cat -A data/raw/customers.csv | head -2
```

Unix line endings (`LF`) — correct:

```
customer_id,customer_name,region,customer_created_date$
1,Aisha Khan,North,2025-10-01$
```

Windows line endings (`CRLF`) — the `^M` is a carriage return that will end up inside your last column:

```
customer_id,customer_name,region,customer_created_date^M$
1,Aisha Khan,North,2025-10-01^M$
```

> **Never `cat` a large file.** It will flood your terminal for minutes. Use `head`, `tail`, or `less` instead.

---

### 4.9 `head` — show the beginning of a file

**Syntax:** `head [options] file...`

| Flag | Meaning |
|------|---------|
| `-n N` | first N lines (default 10) |
| `-c N` | first N bytes |
| `-q` | never print filename headers |

```bash
head -n 3 data/raw/orders.csv
```

```
order_id,customer_id,product_id,order_date,quantity,amount,status,updated_at
101,1,1003,2026-01-05,1,200.00,Completed,2026-01-05 10:00:00
102,1,1002,2026-01-10,2,50.00,Completed,2026-01-10 11:00:00
```

Just the header row — the fastest possible schema check:

```bash
head -n 1 data/raw/orders.csv
```

```
order_id,customer_id,product_id,order_date,quantity,amount,status,updated_at
```

Headers of every raw file at once:

```bash
head -n 1 data/raw/*.csv
```

```
==> data/raw/customers.csv <==
customer_id,customer_name,region,customer_created_date

==> data/raw/orders.csv <==
order_id,customer_id,product_id,order_date,quantity,amount,status,updated_at

==> data/raw/products.csv <==
product_id,product_name,category,unit_price
```

> **Why this matters:** before you start a Spark session (30–60 seconds) to inspect a schema, `head -n 1` gives you the answer in milliseconds. Do this reflexively.

---

### 4.10 `tail` — show the end of a file

**Syntax:** `tail [options] file...`

| Flag | Meaning |
|------|---------|
| `-n N` | last N lines (default 10) |
| `-n +N` | start from line N (useful to skip a header) |
| `-f` | follow — stream new lines as they're appended |
| `-F` | follow, and re-open if the file is rotated |
| `-c N` | last N bytes |

```bash
tail -n 3 data/raw/orders.csv
```

```
106,99,1001,2026-02-10,1,50.00,Completed,2026-02-10 12:00:00
107,1,1003,2026-02-15,1,200.00,Completed,2026-02-15 08:00:00
107,1,1003,2026-02-15,1,200.00,Completed,2026-02-15 08:00:00
```

There is the duplicate order 107, visible immediately.

Skip the header and show only data rows:

```bash
tail -n +2 data/raw/orders.csv
```

```
101,1,1003,2026-01-05,1,200.00,Completed,2026-01-05 10:00:00
102,1,1002,2026-01-10,2,50.00,Completed,2026-01-10 11:00:00
103,2,1001,2026-01-12,1,50.00,Cancelled,2026-01-12 09:30:00
104,3,1004,2026-02-01,1,75.00,Completed,2026-02-01 14:00:00
105,3,1002,2026-02-05,1,,Pending,2026-02-05 15:00:00
106,99,1001,2026-02-10,1,50.00,Completed,2026-02-10 12:00:00
107,1,1003,2026-02-15,1,200.00,Completed,2026-02-15 08:00:00
107,1,1003,2026-02-15,1,200.00,Completed,2026-02-15 08:00:00
```

Notice line 5: `105,3,1002,2026-02-05,1,,Pending,...` — the `,,` is the missing `amount`.

#### Following a running pipeline log

```bash
tail -f logs/pipeline.log
```

```
2026-03-01 06:00:01 INFO  [bronze] Ingesting data/raw/orders.csv
2026-03-01 06:00:03 INFO  [bronze] Wrote 8 rows to data/bronze/orders
2026-03-01 06:00:05 INFO  [silver] Deduplicating on (order_id, product_id)
2026-03-01 06:00:06 WARN  [silver] Removed 1 duplicate row (order_id=107)
2026-03-01 06:00:07 WARN  [silver] 1 row has NULL amount (order_id=105)
2026-03-01 06:00:08 WARN  [silver] 1 row failed customer FK (order_id=106, customer_id=99)
2026-03-01 06:00:12 INFO  [gold] fact_sales rows=4 revenue=525.00
2026-03-01 06:00:12 INFO  [gold] All business rules passed
```

Press `Ctrl+C` to stop following. `tail -f` is how you watch a job in real time — it is the single most-used command during an incident.

---

### 4.11 `grep` — search text with patterns

**Syntax:** `grep [options] pattern [file...]`

| Flag | Meaning |
|------|---------|
| `-i` | case-insensitive |
| `-v` | invert — show non-matching lines |
| `-n` | prefix each match with its line number |
| `-c` | print only the count of matching lines |
| `-r` / `-R` | recurse into directories |
| `-l` | print only names of files containing a match |
| `-L` | print only names of files **without** a match |
| `-w` | match whole words only |
| `-E` | extended regex (`grep -E` == `egrep`) |
| `-F` | fixed strings, no regex — faster and literal |
| `-A N` | show N lines **after** each match |
| `-B N` | show N lines **before** each match |
| `-C N` | show N lines of context around each match |
| `-o` | print only the matching part |
| `--include=GLOB` | restrict recursive search to matching filenames |

Find all Completed orders:

```bash
grep "Completed" data/raw/orders.csv
```

```
101,1,1003,2026-01-05,1,200.00,Completed,2026-01-05 10:00:00
102,1,1002,2026-01-10,2,50.00,Completed,2026-01-10 11:00:00
104,3,1004,2026-02-01,1,75.00,Completed,2026-02-01 14:00:00
106,99,1001,2026-02-10,1,50.00,Completed,2026-02-10 12:00:00
107,1,1003,2026-02-15,1,200.00,Completed,2026-02-15 08:00:00
107,1,1003,2026-02-15,1,200.00,Completed,2026-02-15 08:00:00
```

Six lines — but that includes order 106 (invalid customer) and the duplicate 107. **The shell can find candidates; it cannot apply business rules.** That is exactly why the pipeline exists.

Count them:

```bash
grep -c "Completed" data/raw/orders.csv
```

```
6
```

Everything that is *not* Completed:

```bash
grep -v "Completed" data/raw/orders.csv
```

```
order_id,customer_id,product_id,order_date,quantity,amount,status,updated_at
103,2,1001,2026-01-12,1,50.00,Cancelled,2026-01-12 09:30:00
105,3,1002,2026-02-05,1,,Pending,2026-02-05 15:00:00
```

(The header matched the inverse too — that is why you usually pair `-v` with `tail -n +2`.)

With line numbers:

```bash
grep -n "Pending" data/raw/orders.csv
```

```
6:105,3,1002,2026-02-05,1,,Pending,2026-02-05 15:00:00
```

Find the missing-amount row with a regex (two consecutive commas):

```bash
grep -n ",," data/raw/orders.csv
```

```
6:105,3,1002,2026-02-05,1,,Pending,2026-02-05 15:00:00
```

Find the invalid customer reference — a line whose second field is exactly `99`:

```bash
grep -nE "^[0-9]+,99," data/raw/orders.csv
```

```
7:106,99,1001,2026-02-10,1,50.00,Completed,2026-02-10 12:00:00
```

Multiple patterns with extended regex:

```bash
grep -E "Cancelled|Pending" data/raw/orders.csv
```

```
103,2,1001,2026-01-12,1,50.00,Cancelled,2026-01-12 09:30:00
105,3,1002,2026-02-05,1,,Pending,2026-02-05 15:00:00
```

Search the codebase recursively — "which modules reference `fact_sales`?"

```bash
grep -rn --include="*.py" "fact_sales" src/ notebooks/
```

```
notebooks/06_gold_facts.py:14:FACT_PATH = f"{GOLD}/fact_sales"
notebooks/06_gold_facts.py:88:    fact.write.mode("overwrite").parquet(FACT_PATH)
notebooks/07_data_quality_checks.py:22:    fact = spark.read.parquet("data/gold/fact_sales")
src/quality_checks.py:41:def check_fact_sales_revenue(df, expected: float = 525.00) -> bool:
src/transformations.py:97:    """Build the fact_sales DataFrame at order-line grain."""
```

Which files mention the canonical total?

```bash
grep -rl "525" sql/ src/ tests/ docs/
```

```
sql/03_reporting_queries.sql
src/quality_checks.py
tests/test_gold_facts.py
docs/business_rules.md
```

With context — see the lines around a match:

```bash
grep -n -C 2 "525" src/quality_checks.py
```

```
39-
40-
41:def check_fact_sales_revenue(df, expected: float = 525.00) -> bool:
42-    """Assert recognised revenue matches the canonical business rule."""
43-    actual = df.agg(F.sum("amount")).first()[0]
```

> **`grep` vs `rg`:** modern tooling often provides `ripgrep` (`rg`), which is faster and respects `.gitignore` by default. Learn `grep` first — it exists on every Linux box in the world, including the bare container you'll be debugging at 03:00.

---

### 4.12 `wc` — word, line, character count

**Syntax:** `wc [options] file...`

| Flag | Meaning |
|------|---------|
| `-l` | count lines |
| `-w` | count words |
| `-c` | count bytes |
| `-m` | count characters |
| `-L` | length of the longest line |

```bash
wc -l data/raw/orders.csv
```

```
9 data/raw/orders.csv
```

9 lines = 1 header + 8 data rows. Confirms the sample has 8 order rows (7 distinct order IDs plus the duplicate 107).

All three raw files:

```bash
wc -l data/raw/*.csv
```

```
  5 data/raw/customers.csv
  9 data/raw/orders.csv
  5 data/raw/products.csv
 19 total
```

Full counts:

```bash
wc data/raw/orders.csv
```

```
  9   9 561 data/raw/orders.csv
```

(lines, words, bytes — "words" is 9 because each line has no spaces except in the timestamps... actually each line counts as a couple of whitespace-separated tokens; don't over-read `-w` on CSV.)

Data rows excluding the header:

```bash
tail -n +2 data/raw/orders.csv | wc -l
```

```
8
```

> **Why `wc -l` is a daily tool:** it is your fastest sanity check. "The source had 8 rows, bronze has 8, silver has 7 after dedup, gold has 4 after business rules." Any unexplained row-count change is a bug.

---

### 4.13 `find` — search the filesystem

**Syntax:** `find [path] [tests] [actions]`

| Expression | Meaning |
|---|---|
| `-name "PATTERN"` | match filename (case-sensitive, glob) |
| `-iname "PATTERN"` | match filename, case-insensitive |
| `-type f` / `-type d` | files only / directories only |
| `-size +100M` | larger than 100 MB |
| `-mtime -1` | modified in the last 24 hours |
| `-mmin -30` | modified in the last 30 minutes |
| `-empty` | empty files or directories |
| `-maxdepth N` | don't descend more than N levels |
| `-path "GLOB"` / `-not -path` | match/exclude full paths |
| `-exec CMD {} \;` | run CMD once per result |
| `-exec CMD {} +` | run CMD with many results at once (faster) |
| `-delete` | delete matches (dangerous — preview first) |

Find every CSV in the project:

```bash
cd ~/Learn/customer-sales-lakehouse
find . -type f -name "*.csv"
```

```
./data/raw/customers.csv
./data/raw/orders.csv
./data/raw/products.csv
```

Find every SQL file:

```bash
find sql -type f -name "*.sql" | sort
```

```
sql/01_exploration_queries.sql
sql/02_data_quality_checks.sql
sql/03_reporting_queries.sql
sql/04_star_schema_ddl.sql
```

Find the pipeline notebooks:

```bash
find notebooks -type f -name "0*.py" | sort
```

```
notebooks/01_bronze_ingestion.py
notebooks/02_bronze_validation.py
notebooks/03_silver_transformations.py
notebooks/04_silver_validation.py
notebooks/05_gold_dimensions.py
notebooks/06_gold_facts.py
notebooks/07_data_quality_checks.py
```

Find directories only, one level down:

```bash
find . -maxdepth 1 -type d -not -name "."
```

```
./config
./data
./docs
./notebooks
./orchestration
./sql
./src
./tests
```

Find recently modified Python files:

```bash
find . -name "*.py" -mtime -1 -not -path "./.venv/*"
```

```
./notebooks/06_gold_facts.py
./src/transformations.py
```

Clean up Python caches (the classic `find -exec` idiom):

```bash
# PREVIEW FIRST — never skip this step
find . -type d -name "__pycache__"
```

```
./src/__pycache__
./tests/__pycache__
```

```bash
# Now act
find . -type d -name "__pycache__" -exec rm -rf {} +
```

Combine `find` with `grep` to search only certain files:

```bash
find src notebooks -name "*.py" -exec grep -l "dim_customer" {} +
```

```
notebooks/05_gold_dimensions.py
notebooks/06_gold_facts.py
src/transformations.py
```

> **Safety rule:** `find ... -delete` and `find ... -exec rm` are irreversible. **Always run the `find` without the action first** and read the list. This single habit will save you at least once in your career.

---

## 5. Pipes, redirection, and combining tools

This is where the shell stops being a file browser and becomes a programming environment.

### 5.1 The three standard streams

| Stream | Number | Purpose |
|--------|--------|---------|
| `stdin` | 0 | input |
| `stdout` | 1 | normal output |
| `stderr` | 2 | error output |

Keeping errors separate from data is deliberate: it means you can pipe clean data onward while errors still reach your screen.

### 5.2 Redirection operators

| Operator | Meaning |
|---|---|
| `>` | redirect stdout to a file, **overwriting** it |
| `>>` | redirect stdout to a file, **appending** |
| `2>` | redirect stderr |
| `2>&1` | send stderr to wherever stdout is going |
| `&>` | send both stdout and stderr (bash) |
| `<` | read stdin from a file |
| `\|` | pipe stdout of one command into stdin of the next |
| `\|&` | pipe both stdout and stderr (bash) |
| `tee` | write to a file **and** pass through |

### 5.3 `>` vs `>>`

```bash
cd ~/Learn/customer-sales-lakehouse
mkdir -p scratch

grep "Completed" data/raw/orders.csv > scratch/completed.txt
wc -l scratch/completed.txt
```

```
6 scratch/completed.txt
```

Run the same command again with `>`:

```bash
grep "Completed" data/raw/orders.csv > scratch/completed.txt
wc -l scratch/completed.txt
```

```
6 scratch/completed.txt
```

Still 6 — the file was overwritten. Now with `>>`:

```bash
grep "Completed" data/raw/orders.csv >> scratch/completed.txt
wc -l scratch/completed.txt
```

```
12 scratch/completed.txt
```

12 — appended. **This is exactly the difference between an idempotent pipeline and one that doubles your data on every run.**

> `>` on a file you care about destroys it instantly with no confirmation. In bash you can protect yourself with `set -o noclobber`, which makes `>` refuse to overwrite an existing file (use `>|` to force).

### 5.4 Capturing logs properly

```bash
python notebooks/06_gold_facts.py > logs/gold_facts.log 2>&1
```

Both normal output and errors land in one chronologically ordered file. Order matters: `2>&1 > file` does **not** do the same thing (stderr still goes to the terminal), because redirections are evaluated left to right.

See output *and* save it:

```bash
python notebooks/06_gold_facts.py 2>&1 | tee logs/gold_facts.log
```

```
2026-03-01 06:00:12 INFO  [gold] fact_sales rows=4 revenue=525.00
2026-03-01 06:00:12 INFO  [gold] All business rules passed
```

Append with tee:

```bash
python notebooks/07_data_quality_checks.py 2>&1 | tee -a logs/pipeline.log
```

### 5.5 Pipes

Each `|` connects one command's stdout to the next command's stdin. Programs stream — no intermediate file is created, and memory stays flat even for huge inputs.

#### Counting Completed orders properly

Naive:

```bash
grep -c "Completed" data/raw/orders.csv
```

```
6
```

But that counts the duplicate 107 twice and includes order 106 (invalid customer). Let's build up the correct answer step by step.

**Step 1 — drop the header:**

```bash
tail -n +2 data/raw/orders.csv | wc -l
```

```
8
```

**Step 2 — keep only Completed rows:**

```bash
tail -n +2 data/raw/orders.csv | grep "Completed" | wc -l
```

```
6
```

**Step 3 — remove exact duplicates:**

```bash
tail -n +2 data/raw/orders.csv | grep "Completed" | sort | uniq | wc -l
```

```
5
```

The duplicate 107 is gone. (`uniq` only collapses *adjacent* duplicates, which is why `sort` must come first.)

**Step 4 — exclude the invalid customer reference (customer 99):**

```bash
tail -n +2 data/raw/orders.csv | grep "Completed" | sort | uniq | grep -vE "^[0-9]+,99," | wc -l
```

```
4
```

**Four rows — exactly the row count of `fact_sales`.** ✅

**Step 5 — sum the amounts (field 6) with `awk`:**

```bash
tail -n +2 data/raw/orders.csv \
  | grep "Completed" \
  | sort | uniq \
  | grep -vE "^[0-9]+,99," \
  | awk -F',' '{ total += $6 } END { printf "%.2f\n", total }'
```

```
525.00
```

**The canonical 525.00, derived from nothing but shell commands.** This is a genuinely useful technique: it gives you an independent check on your Spark pipeline. If PySpark says 525.00 and the shell says 525.00, you have two independent implementations agreeing.

#### Seeing the duplicate explicitly

```bash
tail -n +2 data/raw/orders.csv | cut -d',' -f1 | sort | uniq -c | sort -rn
```

```
      2 107
      1 106
      1 105
      1 104
      1 103
      1 102
      1 101
```

Order 107 appears twice. Only duplicated IDs:

```bash
tail -n +2 data/raw/orders.csv | cut -d',' -f1 | sort | uniq -d
```

```
107
```

Exactly one duplicate order_id. ✅ Matches the documented data-quality issue.

#### Status distribution

```bash
tail -n +2 data/raw/orders.csv | cut -d',' -f7 | sort | uniq -c
```

```
      1 Cancelled
      6 Completed
      1 Pending
```

Three distinct statuses, all valid (`Completed`, `Cancelled`, `Pending`). ✅ Zero invalid statuses, as documented.

#### Finding rows with a missing amount

```bash
awk -F',' 'NR > 1 && $6 == "" { print NR": "$0 }' data/raw/orders.csv
```

```
6: 105,3,1002,2026-02-05,1,,Pending,2026-02-05 15:00:00
```

Exactly one. ✅

#### Checking referential integrity in the shell

```bash
# Valid customer IDs from customers.csv
tail -n +2 data/raw/customers.csv | cut -d',' -f1 | sort > scratch/valid_customers.txt
cat scratch/valid_customers.txt
```

```
1
2
3
4
```

```bash
# Customer IDs referenced by orders
tail -n +2 data/raw/orders.csv | cut -d',' -f2 | sort -u > scratch/order_customers.txt

# Referenced but not valid = orphans
comm -23 scratch/order_customers.txt scratch/valid_customers.txt
```

```
99
```

One orphan customer reference. ✅ And for products:

```bash
tail -n +2 data/raw/products.csv | cut -d',' -f1 | sort > scratch/valid_products.txt
tail -n +2 data/raw/orders.csv | cut -d',' -f3 | sort -u > scratch/order_products.txt
comm -23 scratch/order_products.txt scratch/valid_products.txt
```

```
(no output)
```

Zero orphan product references. ✅ Exactly as documented.

### 5.6 Useful companions to the core commands

| Command | Purpose | Example |
|---|---|---|
| `sort` | sort lines | `sort -t',' -k6 -n orders.csv` |
| `uniq` | collapse adjacent duplicates | `sort x \| uniq -c` |
| `cut` | extract fields | `cut -d',' -f1,7 orders.csv` |
| `awk` | field-aware scripting | `awk -F',' '{s+=$6} END{print s}'` |
| `sed` | stream edit | `sed 's/North/East/g' customers.csv` |
| `tr` | translate/delete characters | `tr -d '\r' < in.csv > out.csv` |
| `less` | page through a large file | `less logs/pipeline.log` |
| `comm` | compare two sorted files | `comm -23 a.txt b.txt` |
| `diff` | show differences | `diff expected.csv actual.csv` |
| `xargs` | build command lines from stdin | `find . -name "*.py" \| xargs wc -l` |
| `tee` | split output to file and screen | `cmd \| tee log.txt` |

#### Fixing CRLF line endings (a real Windows problem)

```bash
tr -d '\r' < data/raw/orders.csv > scratch/orders_lf.csv
cat -A scratch/orders_lf.csv | head -2
```

```
order_id,customer_id,product_id,order_date,quantity,amount,status,updated_at$
101,1,1003,2026-01-05,1,200.00,Completed,2026-01-05 10:00:00$
```

No more `^M`. Better long-term fix: configure Git (see §14) and add a `.gitattributes`.

### 5.7 Exit codes and chaining

Every command returns an exit code: `0` means success, anything else means failure.

```bash
grep "Completed" data/raw/orders.csv > /dev/null
echo $?
```

```
0
```

```bash
grep "Refunded" data/raw/orders.csv > /dev/null
echo $?
```

```
1
```

| Operator | Meaning |
|---|---|
| `;` | run the next command regardless |
| `&&` | run the next command **only if** the previous succeeded |
| `\|\|` | run the next command **only if** the previous failed |

```bash
python notebooks/05_gold_dimensions.py && python notebooks/06_gold_facts.py
```

Facts only build if dimensions succeeded — which is exactly the dependency described in `orchestration/pipeline_design.md`.

```bash
# Use exit codes as assertions in a validation script
if [ "$(tail -n +2 data/raw/orders.csv | cut -d',' -f1 | sort | uniq -d | wc -l)" -eq 1 ]; then
    echo "OK: exactly one duplicate order_id, as expected"
else
    echo "FAIL: duplicate count changed"
    exit 1
fi
```

```
OK: exactly one duplicate order_id, as expected
```

> **`set -euo pipefail`** at the top of every bash script: `-e` exit on error, `-u` error on unset variables, `-o pipefail` fail if *any* command in a pipe fails. Without `pipefail`, `false | true` succeeds — and your pipeline silently ignores a failed extract.

---

## 6. Permissions

### 6.1 The rwx model

Every file and directory has three permission sets for three classes of user.

| Class | Who |
|-------|-----|
| **user** (`u`) | the owner of the file |
| **group** (`g`) | members of the file's group |
| **other** (`o`) | everyone else |

| Permission | On a file | On a directory |
|---|---|---|
| `r` (read) | read the contents | list the entries |
| `w` (write) | modify the contents | create/delete/rename entries inside |
| `x` (execute) | run it as a program | **enter** it / traverse through it |

> **The directory `x` bit is the one that confuses everyone.** Without `x` on a directory you cannot `cd` into it or access anything inside it, *even if* you have `r`. `r` without `x` lets you see the names but not the contents. In practice, directories need `x` wherever they need `r`.

### 6.2 Reading the permission string

```bash
cd ~/Learn/customer-sales-lakehouse
ls -l data/raw/orders.csv
```

```
-rw-r--r-- 1 himanshu dataeng 561 Oct  6 09:20 data/raw/orders.csv
```

```
 -   rw-   r--   r--
 │    │     │     │
 │    │     │     └── other: read only
 │    │     └──────── group: read only
 │    └────────────── user:  read + write
 └─────────────────── type:  '-' regular file
```

A directory:

```bash
ls -ld data/raw
```

```
drwxr-xr-x 2 himanshu dataeng 4096 Oct  6 09:20 data/raw
```

`d` = directory. Owner can read, write, and enter. Group and other can read and enter, but not create or delete files.

### 6.3 Octal notation

Each permission has a value; sum them per class.

| Permission | Value |
|---|---|
| `r` | 4 |
| `w` | 2 |
| `x` | 1 |

| Octal | Binary | Symbolic | Meaning |
|---|---|---|---|
| 0 | 000 | `---` | nothing |
| 1 | 001 | `--x` | execute only |
| 2 | 010 | `-w-` | write only |
| 3 | 011 | `-wx` | write + execute |
| 4 | 100 | `r--` | read only |
| 5 | 101 | `r-x` | read + execute |
| 6 | 110 | `rw-` | read + write |
| 7 | 111 | `rwx` | everything |

Common combinations:

| Octal | Symbolic | Typical use |
|---|---|---|
| `644` | `rw-r--r--` | normal data file (CSV, Markdown, Python module) |
| `600` | `rw-------` | private file — secrets, SSH keys, `.env` |
| `755` | `rwxr-xr-x` | directory, or an executable script |
| `750` | `rwxr-x---` | directory shared with your group only |
| `700` | `rwx------` | private directory |
| `775` | `rwxrwxr-x` | group-writable shared project directory |
| `777` | `rwxrwxrwx` | 🚩 **everyone can do anything — almost always wrong** |

### 6.4 `chmod` — change mode

#### Octal form

```bash
chmod 644 data/raw/orders.csv
ls -l data/raw/orders.csv
```

```
-rw-r--r-- 1 himanshu dataeng 561 Oct  6 09:20 data/raw/orders.csv
```

```bash
chmod 755 scripts/run_pipeline.sh
ls -l scripts/run_pipeline.sh
```

```
-rwxr-xr-x 1 himanshu dataeng 1204 Oct  8 14:02 scripts/run_pipeline.sh
```

Now it can be executed:

```bash
./scripts/run_pipeline.sh
```

#### Symbolic form

Syntax: `chmod [who][operator][permissions] file`

| Who | Meaning | | Operator | Meaning |
|---|---|---|---|---|
| `u` | user/owner | | `+` | add |
| `g` | group | | `-` | remove |
| `o` | other | | `=` | set exactly |
| `a` | all (`ugo`) | | | |

```bash
chmod +x scripts/run_pipeline.sh          # add execute for everyone (respecting umask)
chmod u+x scripts/run_pipeline.sh         # add execute for owner only
chmod go-w data/raw/orders.csv            # remove write from group and other
chmod u=rw,go=r data/raw/customers.csv    # set exactly 644
chmod a-x data/raw/*.csv                  # data files should never be executable
```

Verify:

```bash
ls -l data/raw/
```

```
total 12
-rw-r--r-- 1 himanshu dataeng 162 Oct  6 09:20 customers.csv
-rw-r--r-- 1 himanshu dataeng 561 Oct  6 09:20 orders.csv
-rw-r--r-- 1 himanshu dataeng 171 Oct  6 09:20 products.csv
```

#### Recursive, with the `X` trick

```bash
# WRONG: makes every CSV executable too
chmod -R 755 data/

# RIGHT: capital X means "execute only where it already applies, i.e. directories"
chmod -R u=rwX,go=rX data/
```

Result: directories become `755`, files become `644`. This is the idiom to memorise.

### 6.5 `chown` — change owner and group

**Syntax:** `chown [options] user[:group] file...`

| Flag | Meaning |
|---|---|
| `-R` | recursive |
| `-v` | verbose |
| `--from=OLD` | only change files currently owned by OLD |

```bash
sudo chown himanshu:dataeng data/raw/orders.csv
ls -l data/raw/orders.csv
```

```
-rw-r--r-- 1 himanshu dataeng 561 Oct  6 09:20 data/raw/orders.csv
```

Change group only:

```bash
sudo chown :dataeng data/raw/orders.csv
# or equivalently
sudo chgrp dataeng data/raw/orders.csv
```

Recursive — the classic fix after extracting an archive as root:

```bash
sudo chown -R himanshu:dataeng data/
```

Check who you are:

```bash
whoami
id
```

```
himanshu
uid=1000(himanshu) gid=1000(himanshu) groups=1000(himanshu),27(sudo),1001(dataeng)
```

> `chown` to a *different user* requires root. You can change the group to any group you belong to without root.

### 6.6 Why `chmod 777` is a red flag

`chmod 777` grants read, write, and execute to **every account on the system**, including service accounts and anything an attacker compromises.

**Why people do it:** they hit "Permission denied", try `777`, the error goes away, and they move on.

**Why it's wrong:**

| Problem | Consequence |
|---|---|
| Any user or process can **modify your data files** | Silent data corruption with no audit trail |
| Any user can **replace your scripts** | A cron job running `run_pipeline.sh` now executes attacker code — privilege escalation |
| Any user can **delete** your files | Pipeline breaks, and nobody can say who did it |
| World-writable files **fail security audits** (CIS, SOC 2, PCI) | Your platform blocks a release |
| `ssh` and many tools **refuse to work** with over-permissive files | `Permissions 0777 for 'id_rsa' are too open` |
| It hides the real problem | The actual issue is usually ownership or a missing `x` on a parent directory |

**What to do instead:**

1. **Diagnose first.** `ls -l` the file *and every parent directory*. Nine times out of ten the issue is a missing `x` on a parent directory, or wrong ownership.
2. **Fix ownership:** `sudo chown -R $USER:$USER data/`
3. **Use groups for sharing:** `sudo chgrp -R dataeng data/ && chmod -R g+rwX data/`
4. **Grant the minimum:** need to run a script? `chmod u+x` is enough. You do not need `777`.

```bash
# Diagnose a "Permission denied" properly
namei -l data/raw/orders.csv
```

```
f: data/raw/orders.csv
 drwxr-xr-x himanshu dataeng data
 drwx------ root     root    raw        <- here is the real problem
 -rw-r--r-- himanshu dataeng orders.csv
```

The file is fine; the **parent directory** is the problem. The correct fix is `sudo chown himanshu:dataeng data/raw && chmod 755 data/raw` — not `chmod 777`.

### 6.7 Data engineering specifics

| Artefact | Suggested mode | Why |
|---|---|---|
| `data/raw/*.csv` | `644` | readable by the pipeline, writable only by the owner |
| `data/bronze|silver|gold/` | `755` | pipeline writes, analysts read |
| `scripts/*.sh` | `755` | must be executable |
| `src/*.py`, `notebooks/*.py` | `644` | imported/run via `python`, not executed directly |
| `config/dev.yml`, `config/prod.yml` | `640` | may reference accounts; restrict to the group |
| `.env`, credential files | `600` | **owner only, always** |
| `~/.ssh/id_rsa` | `600` | ssh refuses anything looser |
| `logs/` | `755` directory, `644` files | writable by pipeline, readable by on-call |

> **And the rule that outranks all of the above:** never put credentials in a file in the repo, regardless of its permissions. Use environment variables, a cloud key vault, or a managed identity, and reference placeholders like `<storage-account-name>` in committed config.

### 6.8 `umask`

`umask` is the mask subtracted from default permissions for newly created files.

```bash
umask
```

```
0022
```

With `umask 022`: new files get `666 - 022 = 644`, new directories get `777 - 022 = 755`. That is the sensible default. `umask 077` (private by default) is appropriate on a shared server.

---

## 7. Windows equivalents reference

You are on Windows, so you need to read both dialects.

### 7.1 Command translation table

| Task | Unix / Git Bash / WSL | PowerShell cmdlet | PowerShell alias | CMD |
|---|---|---|---|---|
| Print working directory | `pwd` | `Get-Location` | `gl`, `pwd` | `cd` |
| List files | `ls -la` | `Get-ChildItem -Force` | `gci`, `ls`, `dir` | `dir /a` |
| Change directory | `cd path` | `Set-Location path` | `sl`, `cd` | `cd` |
| Make directory | `mkdir -p a/b` | `New-Item -ItemType Directory -Force a\b` | `mkdir` | `mkdir` |
| Copy file | `cp a b` | `Copy-Item a b` | `cpi`, `cp`, `copy` | `copy` |
| Copy tree | `cp -r a b` | `Copy-Item -Recurse a b` | — | `xcopy /e` |
| Move / rename | `mv a b` | `Move-Item a b` | `mi`, `mv`, `move` | `move`/`ren` |
| Delete file | `rm a` | `Remove-Item a` | `ri`, `rm`, `del` | `del` |
| Delete tree | `rm -rf a` | `Remove-Item -Recurse -Force a` | — | `rmdir /s /q` |
| Print whole file | `cat f` | `Get-Content f` | `gc`, `cat`, `type` | `type` |
| First N lines | `head -n 5 f` | `Get-Content f -TotalCount 5` | `gc f -Head 5` | — |
| Last N lines | `tail -n 5 f` | `Get-Content f -Tail 5` | — | — |
| Follow a log | `tail -f f` | `Get-Content f -Wait -Tail 20` | — | — |
| Search text | `grep "x" f` | `Select-String -Pattern "x" -Path f` | `sls` | `find "x" f` |
| Recursive search | `grep -rn "x" src/` | `Select-String -Pattern "x" -Path src\* -Recurse` | — | `findstr /s /n "x" src\*` |
| Count lines | `wc -l f` | `(Get-Content f).Count` | — | — |
| Find files | `find . -name "*.csv"` | `Get-ChildItem -Recurse -Filter *.csv` | `gci -r -filter *.csv` | `dir /s *.csv` |
| Sort | `sort` | `Sort-Object` | `sort` | `sort` |
| Unique | `sort \| uniq` | `Sort-Object -Unique` | — | — |
| Count occurrences | `sort \| uniq -c` | `Group-Object` | `group` | — |
| Pipe | `\|` | `\|` | `\|` | `\|` |
| Redirect (overwrite) | `>` | `>` or `Out-File` | `>` | `>` |
| Redirect (append) | `>>` | `>>` or `Out-File -Append` | `>>` | `>>` |
| Tee | `tee f` | `Tee-Object -FilePath f` | `tee` | — |
| Env var | `$HOME`, `export X=1` | `$env:HOME`, `$env:X = "1"` | — | `%HOME%`, `set X=1` |
| Chain on success | `a && b` | `a; if ($?) { b }` | — | `a && b` |
| Show env | `env` | `Get-ChildItem Env:` | — | `set` |
| Which binary | `which python3` | `Get-Command python` | `gcm` | `where python` |
| Change permissions | `chmod 644 f` | `icacls f /grant ...` | — | `icacls` |
| Change owner | `chown u:g f` | `icacls f /setowner u` | — | `icacls` |

### 7.2 Critical caveats

**PowerShell aliases are NOT the Unix commands.** They point at cmdlets with totally different parameters:

```powershell
ls -l          # ERROR: A parameter cannot be found that matches parameter name 'l'
rm -rf folder  # ERROR: 'rf' is not recognised
grep "x" f     # ERROR: 'grep' is not recognised as a cmdlet
head -5 f      # ERROR
cat -n f       # ERROR
```

**PowerShell pipes objects, not text.** `Get-ChildItem | Where-Object Length -gt 1000` filters on a *property*, not on a string. This is powerful, but it means Unix idioms don't translate line-for-line.

**Path separators.** PowerShell accepts both `\` and `/` in most cases, but `\` is canonical. WSL and Git Bash require `/`. Python's `pathlib` handles both — use it:

```python
from pathlib import Path

RAW = Path("data") / "raw" / "orders.csv"   # correct on every platform
# NOT: "data\\raw\\orders.csv"
```

**Line endings.** Windows uses `CRLF`; Linux uses `LF`. See §14.3 for the Git configuration that prevents this biting you.

### 7.3 Recommended setup for this learning path

> **Install WSL 2 with Ubuntu and do all your terminal work there.** Fall back to Git Bash when you need something quick in a Windows folder. Use PowerShell only for Windows administration.

Rationale:

1. Everything in this guide runs unmodified.
2. Your local environment matches CI (`ubuntu-latest`) and production Spark clusters.
3. You learn the commands you will actually use in a job interview and on the job.
4. VS Code integrates seamlessly via the **WSL** extension — same editor, Linux shell underneath.
5. Python/Java/Spark setup on Ubuntu is far smoother than native Windows Spark (no `winutils.exe`, no `HADOOP_HOME` headaches).

```bash
# One-time setup for the companion project inside WSL
cd ~
mkdir -p Learn && cd Learn
git clone <your-repo-url> customer-sales-lakehouse
cd customer-sales-lakehouse
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 8. Git: the mental model

Most Git confusion comes from not having a model of *where data lives*. Build the model first; the commands then become obvious.

### 8.1 The four places your code can be

```
  ┌──────────────┐  git add    ┌──────────────┐  git commit  ┌──────────────┐  git push   ┌──────────────┐
  │ Working tree │ ──────────> │   Staging    │ ───────────> │  Local repo  │ ──────────> │ Remote repo  │
  │  (your files)│             │ area / index │              │  (.git dir)  │             │  (GitHub)    │
  └──────────────┘ <────────── └──────────────┘ <─────────── └──────────────┘ <────────── └──────────────┘
                   git restore                 git reset                      git fetch
                     --staged                   --soft HEAD~1                 git pull
```

| Place | What it is | How to inspect it |
|-------|-----------|-------------------|
| **Working tree** | the actual files on disk you edit | `ls`, your editor |
| **Staging area (index)** | the proposed contents of the next commit | `git diff --staged` |
| **Local repository** | the full commit history in `.git/` | `git log` |
| **Remote repository** | the shared copy, e.g. on GitHub | `git log origin/main` |

### 8.2 Core concepts defined

#### Repository
A project directory plus a `.git/` subdirectory containing the entire history. Everything Git knows is in `.git/`. Delete it and you have a plain folder with no history.

```bash
cd ~/Learn/customer-sales-lakehouse
ls -la .git | head -8
```

```
total 60
drwxr-xr-x 8 himanshu himanshu 4096 Oct  8 14:30 .
drwxr-xr-x 9 himanshu himanshu 4096 Oct  8 14:30 ..
-rw-r--r-- 1 himanshu himanshu   21 Oct  6 09:00 HEAD
-rw-r--r-- 1 himanshu himanshu  296 Oct  6 09:00 config
drwxr-xr-x 2 himanshu himanshu 4096 Oct  6 09:00 hooks
-rw-r--r-- 1 himanshu himanshu 2810 Oct  8 14:30 index
drwxr-xr-x 4 himanshu himanshu 4096 Oct  8 14:30 objects
drwxr-xr-x 4 himanshu himanshu 4096 Oct  6 09:00 refs
```

#### Working tree
The checked-out files you can see and edit. Files here are in one of four states:

| State | Meaning |
|---|---|
| **Untracked** | Git has never seen this file |
| **Modified** | tracked, and changed since the last commit |
| **Staged** | changes added to the index, ready to commit |
| **Committed/unmodified** | identical to what's in HEAD |

#### Staging area (index)
A holding pen between your working tree and history. It exists so you can commit **a coherent subset** of your changes.

> **Why this matters practically.** You edited `src/transformations.py` (the real fix) and also left a debug `print()` in `notebooks/06_gold_facts.py`. The staging area lets you commit only the fix. One commit, one purpose. This is what makes history reviewable and `git revert` safe.

#### Commit
An immutable snapshot of the whole tree, with metadata: author, committer, timestamp, message, and one or more parent commits. Identified by a SHA-1/SHA-256 hash like `a3f9c21`.

Commits form a **directed acyclic graph** — each points to its parent(s). A branch is just a moving pointer into that graph.

#### HEAD
A pointer to "where you are right now" — normally a branch name, which in turn points to a commit.

```bash
cat .git/HEAD
```

```
ref: refs/heads/feature/add-quality-checks
```

```bash
git rev-parse HEAD
```

```
a3f9c21d8e4b5f6a7c9d0e1f2a3b4c5d6e7f8a9b
```

**Detached HEAD** means HEAD points directly at a commit instead of a branch. Commits you make there belong to no branch and will be garbage-collected unless you create one.

| Reference | Meaning |
|---|---|
| `HEAD` | current commit |
| `HEAD~1` | its parent |
| `HEAD~3` | three commits back along first parents |
| `HEAD^` | first parent (same as `HEAD~1`) |
| `HEAD^2` | **second** parent — only meaningful on a merge commit |
| `main` | the commit `main` points to |
| `origin/main` | your last-known position of `main` on the remote |

#### Branch
A lightweight, movable pointer to a commit — literally a 41-byte file containing a hash. Creating one is instant and free.

```bash
cat .git/refs/heads/main
```

```
8c4e1a9f2b3d5e6a7f8c9d0e1a2b3c4d5e6f7a8b
```

```bash
git branch -vv
```

```
* feature/add-quality-checks a3f9c21 [origin/feature/add-quality-checks] Add FK validation for orders
  main                       8c4e1a9 [origin/main] Add gold star schema DDL
```

#### Remote
A named URL pointing at another copy of the repository. `origin` is the conventional name for the one you cloned from.

```bash
git remote -v
```

```
origin  https://github.com/<your-username>/customer-sales-lakehouse.git (fetch)
origin  https://github.com/<your-username>/customer-sales-lakehouse.git (push)
```

#### Remote-tracking branches
`origin/main` is **your local cache** of where `main` was on the remote the last time you talked to it. It only updates when you `fetch`, `pull`, or `push`. It is not live.

#### Pull request (PR)
A GitHub/GitLab feature, **not a Git feature**. A request to merge your branch into another, plus a place for discussion, code review, and automated checks. In a professional team, PRs are how code gets into `main`.

---

## 9. Git: the core commands in practice

### 9.1 One-time global configuration

```bash
git config --global user.name "Himanshu Tiwari"
git config --global user.email "himanshu@example.com"
git config --global init.defaultBranch main
git config --global core.editor "code --wait"
git config --global pull.rebase false          # merge on pull (explicit, predictable)
git config --global push.default simple
git config --global core.autocrlf input        # see §14.3
git config --global fetch.prune true           # tidy up deleted remote branches

git config --list --show-origin | head
```

Useful aliases:

```bash
git config --global alias.st  "status -sb"
git config --global alias.lg  "log --oneline --graph --decorate --all"
git config --global alias.last "log -1 HEAD --stat"
git config --global alias.unstage "restore --staged"
```

### 9.2 `git init` and `git clone`

```bash
# Start a new repository in an existing folder
cd ~/Learn/customer-sales-lakehouse
git init
```

```
Initialized empty Git repository in /home/himanshu/Learn/customer-sales-lakehouse/.git/
```

```bash
# Or copy an existing one
cd ~/Learn
git clone https://github.com/<your-username>/customer-sales-lakehouse.git
```

```
Cloning into 'customer-sales-lakehouse'...
remote: Enumerating objects: 147, done.
remote: Counting objects: 100% (147/147), done.
remote: Compressing objects: 100% (98/98), done.
remote: Total 147 (delta 42), reused 147 (delta 42), pack-reused 0
Receiving objects: 100% (147/147), 48.21 KiB | 2.41 MiB/s, done.
Resolving deltas: 100% (42/42), done.
```

`clone` does four things at once: creates the directory, initialises `.git`, adds `origin` as a remote, and checks out the default branch.

### 9.3 `git status`

Your most-used command. Run it constantly.

```bash
git status
```

```
On branch feature/add-quality-checks
Your branch is up to date with 'origin/feature/add-quality-checks'.

Changes to be committed:
  (use "git restore --staged <file>..." to unstage)
	modified:   src/quality_checks.py

Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes in working directory)
	modified:   notebooks/07_data_quality_checks.py

Untracked files:
  (use "git add <file>..." to include in what will be committed)
	scratch/
```

Short form:

```bash
git status -sb
```

```
## feature/add-quality-checks...origin/feature/add-quality-checks
M  src/quality_checks.py
 M notebooks/07_data_quality_checks.py
?? scratch/
```

Column 1 is the index, column 2 is the working tree. `M ` = staged. ` M` = modified but not staged. `MM` = staged *and* modified again since. `??` = untracked.

### 9.4 `git add` — stage changes

```bash
git add src/quality_checks.py                 # one file
git add src/                                  # a directory
git add "*.sql"                               # a glob
git add -u                                    # all tracked modifications (not new files)
git add -A                                    # everything, including new and deleted
git add -p                                    # interactive: choose hunk by hunk
```

> **`git add -p` is the skill that separates a good contributor from an average one.** It walks you through each change and asks `Stage this hunk [y,n,q,a,d,s,e,?]?`. Use `s` to split a hunk further. It forces you to re-read your own diff before committing — you will catch leftover debug statements and accidental edits every single time.

```bash
git add -p src/quality_checks.py
```

```
diff --git a/src/quality_checks.py b/src/quality_checks.py
@@ -38,6 +38,14 @@ def check_no_duplicate_orders(df) -> bool:
     return dupes == 0


+def check_fact_sales_revenue(df, expected: float = 525.00) -> bool:
+    """Assert recognised revenue matches the canonical business rule."""
+    actual = df.agg(F.sum("amount")).first()[0]
+    return float(actual) == expected
+
Stage this hunk [y,n,q,a,d,e,?]? y
```

### 9.5 `git diff`

```bash
git diff                        # working tree vs index (unstaged changes)
git diff --staged               # index vs HEAD (what you're about to commit)
git diff HEAD                   # working tree vs HEAD (everything)
git diff main..feature/x        # between two branches
git diff --stat                 # summary only
git diff -- src/transformations.py   # restrict to a path
```

```bash
git diff --staged
```

```diff
diff --git a/src/quality_checks.py b/src/quality_checks.py
index 2a1b3c4..5d6e7f8 100644
--- a/src/quality_checks.py
+++ b/src/quality_checks.py
@@ -38,6 +38,12 @@ def check_no_duplicate_orders(df) -> bool:
     return dupes == 0
 
 
+def check_fact_sales_revenue(df, expected: float = 525.00) -> bool:
+    """Assert recognised revenue matches the canonical business rule."""
+    actual = df.agg(F.sum("amount")).first()[0]
+    return float(actual) == expected
+
+
 def run_all_checks(spark) -> dict[str, bool]:
     """Run every quality check and return a name -> passed mapping."""
```

Reading a diff:

| Marker | Meaning |
|---|---|
| `---` / `+++` | the old and new versions of the file |
| `@@ -38,6 +38,12 @@` | hunk header: old file from line 38 for 6 lines; new file from line 38 for 12 lines |
| ` ` (space) | unchanged context line |
| `-` | removed line |
| `+` | added line |

> **Always run `git diff --staged` before `git commit`.** It is the last chance to catch a hardcoded path, a stray credential, or a debug print.

### 9.6 `git commit`

```bash
git commit -m "Add fact_sales revenue assertion to quality checks"
```

```
[feature/add-quality-checks b7d3e9a] Add fact_sales revenue assertion to quality checks
 1 file changed, 6 insertions(+)
```

Multi-line message (strongly preferred for anything non-trivial):

```bash
git commit
# opens your editor
```

| Flag | Meaning |
|---|---|
| `-m "msg"` | inline message |
| `-a` | auto-stage all **tracked** modified files (skips `git add`) |
| `--amend` | rewrite the most recent commit |
| `--no-verify` | skip pre-commit hooks (use sparingly) |
| `-S` | GPG-sign the commit |

> ⚠️ **`git commit -a` does not add untracked files.** If you created `tests/test_gold_facts.py` and used `-a`, it is **not** in the commit. Always check `git status` afterwards.

> ⚠️ **`--amend` rewrites history.** Safe before you push. After pushing to a shared branch it requires a force push — see §15.

### 9.7 `git log`

```bash
git log --oneline --graph --decorate -n 8
```

```
* b7d3e9a (HEAD -> feature/add-quality-checks, origin/feature/add-quality-checks) Add fact_sales revenue assertion to quality checks
* a3f9c21 Add FK validation for orders against dim_customer
| * 4e8d1c7 (origin/feature/docs) Document medallion layer contracts
|/
* 8c4e1a9 (origin/main, main) Add gold star schema DDL
* 6b2a0f5 Build dim_date generator for 2025-2027
* 5a1c9e3 Deduplicate silver orders on (order_id, product_id)
* 3f8b7d2 Add bronze ingestion for raw CSVs
* 1d4e6a0 Initial commit: project skeleton
```

Other useful forms:

```bash
git log -p -- src/transformations.py     # full diffs for one file
git log --since="2 weeks ago"
git log --author="Himanshu"
git log --grep="revenue"                 # search commit messages
git log --stat -n 3                      # files changed per commit
git log main..feature/x                  # commits on the branch not yet in main
git log --follow -- src/config.py        # follow the file across renames
```

Who changed a line and why:

```bash
git blame -L 40,46 src/quality_checks.py
```

```
b7d3e9a1 (Himanshu Tiwari 2026-03-01 14:02:11 +0530 40) def check_fact_sales_revenue(df, expected: float = 525.00) -> bool:
b7d3e9a1 (Himanshu Tiwari 2026-03-01 14:02:11 +0530 41)     """Assert recognised revenue matches the canonical business rule."""
b7d3e9a1 (Himanshu Tiwari 2026-03-01 14:02:11 +0530 42)     actual = df.agg(F.sum("amount")).first()[0]
```

```bash
git show b7d3e9a                 # full details of one commit
```

### 9.8 Undoing things (safely)

| Goal | Command | Destructive? |
|---|---|---|
| Unstage a file, keep edits | `git restore --staged <file>` | no |
| Discard working-tree edits to a file | `git restore <file>` | **yes** — edits are gone |
| Restore a file from a specific commit | `git restore --source=HEAD~2 <file>` | overwrites working copy |
| Undo the last commit, keep changes staged | `git reset --soft HEAD~1` | no |
| Undo the last commit, keep changes unstaged | `git reset --mixed HEAD~1` | no |
| Undo the last commit and **destroy** changes | `git reset --hard HEAD~1` | **yes** |
| Create a new commit that reverses an old one | `git revert <sha>` | no — safe on shared branches |
| Park work in progress | `git stash` / `git stash pop` | no |

```bash
# Undo the last commit but keep everything staged — the common "oops, wrong message" fix
git reset --soft HEAD~1
git commit -m "Add fact_sales revenue assertion (BR-08) to quality checks"
```

```bash
# Safely reverse a commit that is already on main
git revert a3f9c21
```

```
[main 9f2e4d1] Revert "Add FK validation for orders against dim_customer"
 1 file changed, 12 deletions(-)
```

> **The rule: `reset` on private history, `revert` on shared history.** `reset` rewrites; `revert` adds. Never rewrite history that other people have pulled.

### 9.9 `git stash`

```bash
git stash push -m "wip: experimenting with SCD2 lookup"
```

```
Saved working directory and index state On feature/x: wip: experimenting with SCD2 lookup
```

```bash
git stash list
```

```
stash@{0}: On feature/x: wip: experimenting with SCD2 lookup
```

```bash
git stash pop          # apply and remove from the stash
git stash apply        # apply but keep it in the stash
git stash drop         # discard
git stash -u           # include untracked files
```

### 9.10 `fetch`, `pull`, `push`

#### `git fetch` — download, don't change anything

```bash
git fetch origin
```

```
remote: Enumerating objects: 9, done.
From https://github.com/<your-username>/customer-sales-lakehouse
   8c4e1a9..d2f7b31  main       -> origin/main
```

Your working tree is untouched. Only `origin/main` moved. Now inspect before you integrate:

```bash
git log --oneline HEAD..origin/main
```

```
d2f7b31 Add reporting queries for revenue by region
```

> **`fetch` is always safe.** When you're unsure what's happening, `fetch` then `log` is the right move.

#### `git pull` — fetch + integrate

```bash
git pull origin main
```

```
From https://github.com/<your-username>/customer-sales-lakehouse
 * branch            main       -> FETCH_HEAD
Updating 8c4e1a9..d2f7b31
Fast-forward
 sql/03_reporting_queries.sql | 24 ++++++++++++++++++++++++
 1 file changed, 24 insertions(+)
```

`git pull` = `git fetch` + `git merge` (or `git rebase` with `--rebase`). Because it changes your working tree, it can produce conflicts. Prefer `fetch` + look + `merge` while you're learning.

#### `git push` — upload

```bash
git push origin feature/add-quality-checks
```

```
Enumerating objects: 9, done.
Counting objects: 100% (9/9), done.
Delta compression using up to 8 threads
Compressing objects: 100% (5/5), done.
Writing objects: 100% (5/5), 721 bytes | 721.00 KiB/s, done.
Total 5 (delta 3), reused 0 (delta 0), pack-reused 0
remote: Resolving deltas: 100% (3/3), completed with 3 local objects.
To https://github.com/<your-username>/customer-sales-lakehouse.git
   a3f9c21..b7d3e9a  feature/add-quality-checks -> feature/add-quality-checks
```

First push of a new branch — set upstream so later pushes are bare `git push`:

```bash
git push -u origin feature/add-quality-checks
```

If push is rejected:

```
 ! [rejected]        main -> main (fetch first)
error: failed to push some refs to '...'
hint: Updates were rejected because the remote contains work that you do
hint: not have locally.
```

**The correct response is `git pull` (or `fetch` + `merge`), resolve anything that conflicts, then push again.** It is *never* `git push --force` on a shared branch.

---

## 10. Branching, merging, rebasing

### 10.1 Branch commands

```bash
git branch                              # list local branches
git branch -a                           # include remote-tracking branches
git branch -vv                          # show upstream and last commit
git branch feature/scd2-dim-customer    # create (without switching)
git switch feature/scd2-dim-customer    # switch to it
git switch -c feature/scd2-dim-customer # create and switch (modern)
git checkout -b feature/scd2-dim-customer  # create and switch (classic)
git switch -                            # go back to the previous branch
git branch -d feature/old               # delete (refuses if unmerged)
git branch -D feature/old               # force delete (discards commits)
git push origin --delete feature/old    # delete the remote branch
```

> `git switch` (branches) and `git restore` (files) were introduced to split the overloaded `git checkout`. Prefer them; recognise `checkout` in older docs.

```bash
git switch -c feature/scd2-dim-customer
```

```
Switched to a new branch 'feature/scd2-dim-customer'
```

### 10.2 Branch naming conventions

| Prefix | Use for | Example |
|---|---|---|
| `feature/` | new functionality | `feature/scd2-dim-customer` |
| `fix/` | bug fixes | `fix/duplicate-order-107` |
| `docs/` | documentation only | `docs/medallion-contracts` |
| `refactor/` | restructuring, no behaviour change | `refactor/extract-config-loader` |
| `test/` | tests only | `test/fact-sales-revenue-assertion` |
| `chore/` | tooling, deps, CI | `chore/bump-pyspark-3.5.1` |

Rules: lowercase, hyphen-separated, descriptive, short. Include a ticket ID if your team uses one: `feature/DATA-412-scd2-dim-customer`.

### 10.3 Merge

A merge takes two branches and produces a commit with two parents.

#### Fast-forward merge

If `main` hasn't moved since you branched, Git just slides the pointer forward:

```
Before:        A---B---C  (main)
                        \
                         D---E  (feature)

git switch main && git merge feature

After:         A---B---C---D---E  (main, feature)
```

```bash
git switch main
git merge feature/add-quality-checks
```

```
Updating 8c4e1a9..b7d3e9a
Fast-forward
 src/quality_checks.py | 6 ++++++
 1 file changed, 6 insertions(+)
```

#### Three-way merge

If `main` has moved, Git creates a merge commit:

```
Before:        A---B---C---F---G  (main)
                        \
                         D---E  (feature)

git switch main && git merge feature

After:         A---B---C---F---G---M  (main)
                        \         /
                         D---E---'  (feature)
```

```bash
git merge feature/add-quality-checks
```

```
Merge made by the 'ort' strategy.
 src/quality_checks.py | 6 ++++++
 1 file changed, 6 insertions(+)
```

Force a merge commit even when fast-forward is possible (keeps the branch visible in history):

```bash
git merge --no-ff feature/add-quality-checks
```

### 10.4 Rebase

Rebase **replays** your commits on top of another branch, producing a linear history.

```
Before:        A---B---C---F---G  (main)
                        \
                         D---E  (feature)

git switch feature && git rebase main

After:         A---B---C---F---G  (main)
                                \
                                 D'---E'  (feature)
```

```bash
git switch feature/add-quality-checks
git rebase main
```

```
Successfully rebased and updated refs/heads/feature/add-quality-checks.
```

Note `D'` and `E'`: these are **new commits with new SHAs**. The originals are orphaned (but recoverable via `reflog`).

#### Interactive rebase — clean up before you open a PR

```bash
git rebase -i main
```

```
pick a3f9c21 Add FK validation for orders
squash b7d3e9a fix typo
squash c1d2e3f actually fix the typo
pick d4e5f6a Add revenue assertion

# Commands:
# p, pick   = use commit
# r, reword = use commit, but edit the message
# e, edit   = use commit, but stop for amending
# s, squash = meld into previous commit
# f, fixup  = like squash, but discard this commit's message
# d, drop   = remove commit
```

This turns four messy commits into two clean ones. **Do this before every PR.** Reviewers read your commits; make them worth reading.

### 10.5 Merge vs rebase

| Aspect | Merge | Rebase |
|---|---|---|
| History shape | branching graph, preserves reality | linear, easy to read |
| Creates new SHAs? | no | **yes** — rewrites commits |
| Safe on shared branches? | **yes** | **no** |
| Preserves exact context of development | yes | no (commits are replayed) |
| Conflict resolution | once, at the merge | potentially once **per commit** |
| `git log` readability | noisier | cleaner |
| Easy to revert the whole feature | yes (`git revert -m 1 <merge>`) | harder |
| Debugging with `git bisect` | fine | slightly cleaner |

### 10.6 The golden rule of rebasing

> **Never rebase commits that exist outside your local repository and that someone else may have based work on.**

If you rebase a branch you've already pushed, and a colleague has pulled it, their history and yours diverge irreconcilably. They will have to do painful surgery. On a branch only you work on, rebasing before a PR is good practice.

**A sane team policy:**

- Rebase your **own feature branch** onto `main` to stay current and to tidy commits before review.
- **Merge** feature branches into `main` (ideally via PR, with a squash or merge commit).
- **Never** rebase `main` itself.

---

## 11. A worked merge conflict

Conflicts are normal. They happen when two branches change **the same lines of the same file**. Git cannot know which is right, so it asks you.

### 11.1 Set up the conflict

Current `sql/03_reporting_queries.sql` on `main`:

```sql
-- Total recognised revenue
SELECT SUM(amount) AS total_revenue
FROM fact_sales;
```

**Branch A — `feature/revenue-by-region`:**

```bash
git switch -c feature/revenue-by-region main
```

Edit the file to:

```sql
-- Total recognised revenue by region
SELECT c.region, SUM(f.amount) AS total_revenue
FROM fact_sales f
JOIN dim_customer c ON c.customer_key = f.customer_key
GROUP BY c.region;
```

```bash
git add sql/03_reporting_queries.sql
git commit -m "Add region breakdown to revenue report"
```

**Branch B — `feature/revenue-by-category`, branched from the same point:**

```bash
git switch -c feature/revenue-by-category main
```

Edit the *same lines* to:

```sql
-- Total recognised revenue by product category
SELECT p.category, SUM(f.amount) AS total_revenue
FROM fact_sales f
JOIN dim_product p ON p.product_key = f.product_key
GROUP BY p.category;
```

```bash
git add sql/03_reporting_queries.sql
git commit -m "Add category breakdown to revenue report"
```

### 11.2 Merge A into main (clean)

```bash
git switch main
git merge feature/revenue-by-region
```

```
Updating 8c4e1a9..e1f2a3b
Fast-forward
 sql/03_reporting_queries.sql | 5 +++--
 1 file changed, 3 insertions(+), 2 deletions(-)
```

### 11.3 Merge B into main (conflict)

```bash
git merge feature/revenue-by-category
```

```
Auto-merging sql/03_reporting_queries.sql
CONFLICT (content): Merge conflict in sql/03_reporting_queries.sql
Automatic merge failed; fix conflicts and then commit the result.
```

```bash
git status
```

```
On branch main
You have unmerged paths.
  (fix conflicts and run "git commit")
  (use "git merge --abort" to abort the merge)

Unmerged paths:
  (use "git add <file>..." to mark resolution)
	both modified:   sql/03_reporting_queries.sql

no changes added to commit (use "git add" and/or "git commit -a")
```

### 11.4 The conflict markers

```bash
cat sql/03_reporting_queries.sql
```

```sql
<<<<<<< HEAD
-- Total recognised revenue by region
SELECT c.region, SUM(f.amount) AS total_revenue
FROM fact_sales f
JOIN dim_customer c ON c.customer_key = f.customer_key
GROUP BY c.region;
=======
-- Total recognised revenue by product category
SELECT p.category, SUM(f.amount) AS total_revenue
FROM fact_sales f
JOIN dim_product p ON p.product_key = f.product_key
GROUP BY p.category;
>>>>>>> feature/revenue-by-category
```

Decode the markers:

| Marker | Meaning |
|---|---|
| `<<<<<<< HEAD` | start of **your** version (the branch you are merging *into*) |
| `=======` | divider |
| `>>>>>>> feature/revenue-by-category` | end of **their** version (the branch being merged *in*) |

For extra context, turn on diff3 style, which also shows the common ancestor:

```bash
git config --global merge.conflictstyle zdiff3
```

```sql
<<<<<<< HEAD
-- Total recognised revenue by region
SELECT c.region, SUM(f.amount) AS total_revenue
...
||||||| merged common ancestor
-- Total recognised revenue
SELECT SUM(amount) AS total_revenue
FROM fact_sales;
=======
-- Total recognised revenue by product category
...
>>>>>>> feature/revenue-by-category
```

Now you can see *what each side changed*, which usually makes the right answer obvious.

### 11.5 Resolve it

**Step 1 — Understand both intentions.** Branch A wants region breakdown. Branch B wants category breakdown. Neither is wrong; the file should contain **both queries**.

**Step 2 — Edit the file.** Delete every marker line and write the correct final content:

```sql
-- Total recognised revenue (canonical business rule: 525.00 for the sample data)
SELECT SUM(amount) AS total_revenue
FROM fact_sales;

-- Total recognised revenue by region
SELECT c.region, SUM(f.amount) AS total_revenue
FROM fact_sales f
JOIN dim_customer c ON c.customer_key = f.customer_key
GROUP BY c.region
ORDER BY total_revenue DESC;

-- Total recognised revenue by product category
SELECT p.category, SUM(f.amount) AS total_revenue
FROM fact_sales f
JOIN dim_product p ON p.product_key = f.product_key
GROUP BY p.category
ORDER BY total_revenue DESC;
```

**Step 3 — Verify no markers remain.** This is the step people skip, and it is how `<<<<<<<` ends up on `main`:

```bash
grep -n -E "^(<<<<<<<|=======|>>>>>>>|\|\|\|\|\|\|\|)" sql/03_reporting_queries.sql
```

```
(no output)
```

Check the whole repo:

```bash
git diff --check
grep -rn "<<<<<<< HEAD" . --exclude-dir=.git
```

**Step 4 — Actually test the result.** A syntactically merged file can still be semantically broken:

```bash
python notebooks/07_data_quality_checks.py
```

```
2026-03-01 14:22:03 INFO  [checks] fact_sales rows=4
2026-03-01 14:22:03 INFO  [checks] recognised revenue=525.00
2026-03-01 14:22:03 INFO  [checks] revenue by region: North=525.00
2026-03-01 14:22:03 INFO  [checks] revenue by category: Hardware=400.00, Accessories=125.00
2026-03-01 14:22:03 INFO  [checks] All business rules passed
```

Both breakdowns still total **525.00**. ✅ The merge is correct.

**Step 5 — Stage the resolved file.**

```bash
git add sql/03_reporting_queries.sql
git status
```

```
On branch main
All conflicts fixed but you are still merging.
  (use "git commit" to conclude merge)

Changes to be committed:
	modified:   sql/03_reporting_queries.sql
```

**Step 6 — Commit the merge.**

```bash
git commit -m "Merge feature/revenue-by-category: keep both region and category breakdowns

Both branches edited the same reporting query block. Resolved by keeping
all three queries: the headline total plus the region and category
breakdowns. Verified all three reconcile to 525.00."
```

```
[main 7a9c3d1] Merge feature/revenue-by-category: keep both region and category breakdowns
```

```bash
git log --oneline --graph -n 5
```

```
*   7a9c3d1 (HEAD -> main) Merge feature/revenue-by-category: keep both region and category breakdowns
|\
| * 5b8e2c4 (feature/revenue-by-category) Add category breakdown to revenue report
* | e1f2a3b (feature/revenue-by-region) Add region breakdown to revenue report
|/
* 8c4e1a9 Add gold star schema DDL
```

### 11.6 Conflict escape hatches

| Situation | Command |
|---|---|
| Abandon the merge entirely, go back to pre-merge state | `git merge --abort` |
| Abandon a rebase | `git rebase --abort` |
| Continue a rebase after resolving | `git rebase --continue` |
| Accept **your** version wholesale for one file | `git checkout --ours <file>` |
| Accept **their** version wholesale for one file | `git checkout --theirs <file>` |
| See the three versions of a conflicted file | `git show :1:<file>` (base), `:2:` (ours), `:3:` (theirs) |
| Open a visual merge tool | `git mergetool` |

> ⚠️ **During a `rebase`, "ours" and "theirs" are swapped** relative to a merge, because rebase replays *your* commits onto *their* branch. Read the `git status` output carefully rather than trusting muscle memory.

### 11.7 How to have fewer conflicts

1. **Keep branches short-lived.** A branch that lives two days conflicts far less than one that lives two weeks.
2. **Pull `main` into your branch frequently** (or rebase onto it) so divergence stays small.
3. **Keep PRs small and focused.** One concern per branch.
4. **Agree on formatting.** Run `black`/`ruff format` so nobody's editor reflows a file and conflicts with everything.
5. **Don't commit generated files.** Parquet, `.ipynb` outputs, and `__pycache__` are conflict factories. See §14.
6. **Talk to your team** before refactoring a file everyone touches.

---

## 12. The standard workflow for this project

The complete loop, start to finish, for adding SCD2 support to `dim_customer`.

### Step 1 — Sync `main`

```bash
cd ~/Learn/customer-sales-lakehouse
git switch main
git pull origin main
```

```
Already up to date.
```

> Always start from fresh `main`. Branching from a stale `main` is how you create conflicts for yourself on day one.

### Step 2 — Create a branch

```bash
git switch -c feature/scd2-dim-customer
```

```
Switched to a new branch 'feature/scd2-dim-customer'
```

### Step 3 — Make the change

Edit `src/transformations.py`, `notebooks/05_gold_dimensions.py`, `sql/04_star_schema_ddl.sql`, and add `tests/test_scd2_dim_customer.py`.

### Step 4 — Check what you changed

```bash
git status -sb
```

```
## feature/scd2-dim-customer
 M notebooks/05_gold_dimensions.py
 M sql/04_star_schema_ddl.sql
 M src/transformations.py
?? tests/test_scd2_dim_customer.py
?? data/gold/dim_customer/
?? scratch/debug.log
```

Note the junk: `data/gold/` output and `scratch/debug.log` must **not** be committed. This is exactly what `.gitignore` is for (§14).

### Step 5 — Run tests before committing

```bash
python -m pytest tests/ -q
```

```
........                                                      [100%]
8 passed in 12.43s
```

```bash
python notebooks/07_data_quality_checks.py | tail -3
```

```
2026-03-01 15:10:44 INFO  [checks] fact_sales rows=4
2026-03-01 15:10:44 INFO  [checks] recognised revenue=525.00
2026-03-01 15:10:44 INFO  [checks] All business rules passed
```

**The revenue is still 525.00.** Your refactor did not change the numbers — that is the whole point of a regression test.

### Step 6 — Stage deliberately

```bash
git add src/transformations.py notebooks/05_gold_dimensions.py sql/04_star_schema_ddl.sql tests/test_scd2_dim_customer.py
git status -sb
```

```
## feature/scd2-dim-customer
M  notebooks/05_gold_dimensions.py
M  sql/04_star_schema_ddl.sql
M  src/transformations.py
A  tests/test_scd2_dim_customer.py
?? data/gold/dim_customer/
?? scratch/debug.log
```

Four files staged, the junk correctly left out.

### Step 7 — Review the diff one last time

```bash
git diff --staged --stat
```

```
 notebooks/05_gold_dimensions.py   | 48 +++++++++++++++++++++++++++----
 sql/04_star_schema_ddl.sql        | 12 ++++++--
 src/transformations.py            | 63 ++++++++++++++++++++++++++++++++++-----
 tests/test_scd2_dim_customer.py   | 41 ++++++++++++++++++++++++++
 4 files changed, 148 insertions(+), 16 deletions(-)
```

```bash
git diff --staged | grep -nE "print\(|TODO|FIXME|password|secret|token"
```

```
(no output)
```

> **Make that grep a habit.** It catches debug prints and, more importantly, accidental credentials — before they are in history forever.

### Step 8 — Commit with a real message

```bash
git commit
```

```
Add SCD Type 2 history tracking to dim_customer

The customer region drives revenue attribution in fact_sales. Under the
previous SCD1 overwrite behaviour, a customer changing region silently
restated all of their historical revenue into the new region, so prior
period reports were not reproducible.

Add effective_from, effective_to and is_current to dim_customer, expire
the current row on a tracked-attribute change, and insert a new version.
Fact loads now resolve customer_key with an as-of join on order_date
rather than filtering is_current, so history stays attached to the
region that was true when the order was placed.

Tracked attributes: region (SCD2). customer_name remains SCD1 because
name changes are corrections, not business events.

Verified recognised revenue is unchanged at 525.00 for the sample data.

Refs: DATA-412
```

```
[feature/scd2-dim-customer 3c7f1a9] Add SCD Type 2 history tracking to dim_customer
 4 files changed, 148 insertions(+), 16 deletions(-)
 create mode 100644 tests/test_scd2_dim_customer.py
```

### Step 9 — Push

```bash
git push -u origin feature/scd2-dim-customer
```

```
Enumerating objects: 18, done.
Writing objects: 100% (11/11), 3.42 KiB | 3.42 MiB/s, done.
To https://github.com/<your-username>/customer-sales-lakehouse.git
 * [new branch]      feature/scd2-dim-customer -> feature/scd2-dim-customer
branch 'feature/scd2-dim-customer' set up to track 'origin/feature/scd2-dim-customer'.

remote: Create a pull request for 'feature/scd2-dim-customer' on GitHub by visiting:
remote:      https://github.com/<your-username>/customer-sales-lakehouse/pull/new/feature/scd2-dim-customer
```

### Step 10 — Open a pull request

Either follow that URL, or use the GitHub CLI:

```bash
gh pr create \
  --title "Add SCD Type 2 history tracking to dim_customer" \
  --body-file .github/pull_request_template.md \
  --base main
```

A good PR description answers four questions:

```markdown
## What
Adds SCD Type 2 versioning to `dim_customer` (effective_from, effective_to,
is_current) and switches the `fact_sales` load to an as-of surrogate key lookup.

## Why
Region drives revenue attribution. With SCD1, a customer moving region
retroactively restated historical revenue, so prior-period reports were not
reproducible. See `docs/business_rules.md` BR-10.

## How
- `dim_customer`: expire the current row on a tracked-attribute change, insert
  a new version with a fresh surrogate key.
- `fact_sales`: join on `order_date BETWEEN effective_from AND effective_to`
  instead of `is_current`.
- Partial unique index enforces exactly one current row per `customer_id`.

## Testing
- `pytest tests/` — 8 passed.
- `notebooks/07_data_quality_checks.py` — recognised revenue still **525.00**,
  fact_sales still 4 rows.
- Manual scenario: moved customer 3 (Carla Jones) North -> East on 2026-03-01;
  February revenue correctly remains attributed to North.

## Risk / rollback
Gold layer is a full reload, so rollback is `git revert` plus a pipeline re-run.
No change to bronze or silver.
```

### Step 11 — Review

As an **author**:

- Read your own diff on GitHub before requesting review. You will find something.
- Respond to every comment, even if just "done" or "good catch, fixed in 9f3a2c1".
- Push follow-up commits; do not force-push mid-review, as it destroys reviewers' "changes since last review".

As a **reviewer**:

- Does it do what the description says?
- Are business rules preserved? **Does revenue still equal 525.00?**
- Are there tests for the new behaviour?
- Any hardcoded paths, credentials, or `print()` debris?
- Is it readable by someone who has never seen it?

Pull the branch locally to actually run it:

```bash
git fetch origin
git switch feature/scd2-dim-customer
python -m pytest tests/ -q
```

### Step 12 — Merge

Once approved and CI is green, merge via the GitHub UI or:

```bash
gh pr merge --squash --delete-branch
```

| Strategy | Result | Use when |
|---|---|---|
| **Merge commit** | preserves every commit + adds a merge commit | the individual commits are meaningful |
| **Squash and merge** | one commit on `main` | the branch has messy WIP commits (most common) |
| **Rebase and merge** | replays commits linearly, no merge commit | you want strictly linear history |

### Step 13 — Clean up

```bash
git switch main
git pull origin main
git branch -d feature/scd2-dim-customer
```

```
Deleted branch feature/scd2-dim-customer (was 3c7f1a9).
```

```bash
git fetch --prune                       # drop stale origin/* refs
git branch -a
```

```
* main
  remotes/origin/HEAD -> origin/main
  remotes/origin/main
```

Clean. Repeat from Step 1 for the next piece of work.

---

## 13. Commit message conventions

### 13.1 The seven rules

1. Separate the subject from the body with a blank line.
2. Limit the subject line to ~50 characters (hard limit 72).
3. Capitalise the subject line.
4. Do not end the subject line with a period.
5. Use the **imperative mood** in the subject: "Add", not "Added" or "Adds".
6. Wrap the body at 72 characters.
7. Use the body to explain **what** and **why**, not *how*. The diff shows how.

> **The imperative-mood test:** a good subject completes the sentence *"If applied, this commit will ___."*
> "If applied, this commit will **add SCD2 history tracking to dim_customer**." ✅
> "If applied, this commit will **added scd2 stuff**." ❌

### 13.2 Conventional Commits

A widely used, machine-parseable format:

```
<type>(<scope>): <subject>

<body>

<footer>
```

| Type | Use for |
|---|---|
| `feat` | a new feature |
| `fix` | a bug fix |
| `docs` | documentation only |
| `test` | adding or fixing tests |
| `refactor` | restructuring with no behaviour change |
| `perf` | performance improvement |
| `chore` | build, tooling, dependencies |
| `ci` | CI configuration |
| `revert` | reverts a previous commit |

```
feat(gold): add SCD2 history tracking to dim_customer

fix(silver): deduplicate orders on (order_id, product_id)

docs(modelling): document the 525.00 recognised-revenue rule

test(gold): assert fact_sales revenue equals 525.00

chore(deps): bump pyspark from 3.5.0 to 3.5.1

refactor(config): load environment paths from config/dev.yml

feat(gold)!: change fact_sales grain to order line

BREAKING CHANGE: fact_sales now has one row per order line rather than
per order. Downstream reports aggregating without GROUP BY order_id will
double count. Migration guide in docs/migrations/2026-03-fact-grain.md.
```

### 13.3 Bad vs good

| ❌ Bad | Why it's bad | ✅ Good |
|---|---|---|
| `fix` | fixes what? | `fix(silver): drop duplicate order_id 107 before gold load` |
| `update file` | which file, and why? | `refactor(src): extract config loading into config.py` |
| `asdf` | zero information | `chore(repo): remove committed Parquet output from data/gold` |
| `Fixed the bug where the pipeline was failing because of the duplicate orders in the source file which caused the gold layer to have too many rows` | 140-char subject, no body | `fix(silver): deduplicate orders before the gold load` + body |
| `WIP` | should never reach `main` | squash it before merging |
| `changes` | meaningless | `feat(gold): add dim_date covering 2025-2027` |
| `Added new quality checks` | past tense | `test(quality): add FK and null-amount checks for orders` |
| `final version` | commits are not file versions | `feat(reporting): add revenue by region and category queries` |
| `fix typo` (×6 in a row) | noise | squash into the commit being fixed |
| `merge` | which merge, resolved how? | `Merge feature/revenue-by-category: keep both breakdowns` |

### 13.4 A full worked example

```
fix(silver): deduplicate orders before the gold load

The source extract emits byte-identical duplicate rows when the upstream
job retries. Order 107 appears twice in orders.csv with the same
updated_at, which inflated recognised revenue from 525.00 to 725.00.

Deduplicate in silver with row_number() over
(order_id, product_id) ordered by updated_at DESC, so that a genuine
later revision of a row wins and an exact retry duplicate is dropped.
Also add a UNIQUE (order_id, product_key) constraint on fact_sales so
the database rejects this class of error even if the transform regresses.

Verified recognised revenue is back to 525.00 across the region, category
and customer breakdowns.

Fixes #47
Refs: DATA-388
```

This message tells a future engineer — possibly you at 03:00 during an incident — exactly what happened, why, and how it was verified.

### 13.5 Linking to issues

| Footer | Effect on GitHub |
|---|---|
| `Fixes #47` | closes issue 47 when merged to the default branch |
| `Closes #47` | same |
| `Resolves #47` | same |
| `Refs #47` | links without closing |
| `Co-authored-by: Name <email>` | credits a pair-programming partner |

---

## 14. `.gitignore`

### 14.1 What it does

`.gitignore` lists path patterns Git should not track. It only affects **untracked** files — a file already committed stays tracked until you explicitly remove it from the index.

### 14.2 A realistic `.gitignore` for this project

```gitignore
# ---- Python ----
__pycache__/
*.py[cod]
*.egg-info/
.eggs/
build/
dist/

# ---- Virtual environments ----
.venv/
venv/
env/
ENV/

# ---- Testing & coverage ----
.pytest_cache/
.coverage
coverage.xml
htmlcov/
.tox/
.mypy_cache/
.ruff_cache/

# ---- Jupyter ----
.ipynb_checkpoints/
*.ipynb_checkpoints

# ---- Spark / Hadoop local artefacts ----
spark-warehouse/
metastore_db/
derby.log
*.crc
_SUCCESS

# ---- Pipeline OUTPUT: never commit generated data ----
data/bronze/
data/silver/
data/gold/
logs/
scratch/
*.parquet
*.delta

# ---- Keep the raw inputs: they are small, fictional, and part of the project ----
!data/raw/
!data/raw/*.csv

# ---- Secrets: NEVER commit these ----
.env
.env.*
*.pem
*.key
*.pfx
credentials.json
secrets.yml
config/local.yml

# ---- IDE / editor ----
.vscode/
.idea/
*.swp
*~

# ---- OS noise ----
.DS_Store
Thumbs.db
desktop.ini
```

### 14.3 Pattern syntax

| Pattern | Matches |
|---|---|
| `*.parquet` | any file with that extension, at any depth |
| `data/gold/` | the directory and everything under it |
| `/build` | `build` at the repository root only |
| `**/logs` | `logs` at any depth |
| `!data/raw/*.csv` | **negation** — re-include something an earlier rule excluded |
| `debug?.log` | `debug1.log`, `debugA.log` — `?` is one character |
| `*.log` then `!important.log` | ignore all logs except one |
| `# comment` | a comment |

> **Order matters for negations.** A `!` rule cannot re-include a file if its *parent directory* is excluded. `data/` followed by `!data/raw/*.csv` will not work — you must not exclude the parent, or you must re-include it explicitly (`!data/raw/`).

### 14.4 Why committing generated data is harmful

| Problem | Explanation |
|---|---|
| Repository bloat | Parquet files are binary; Git stores a full new blob per change. Repos balloon to gigabytes. |
| Useless diffs | `git diff` on a Parquet file is unreadable noise. |
| Guaranteed conflicts | Every teammate regenerates a byte-different file. Every merge conflicts. |
| Wrong source of truth | Derived data must be *reproducible from code*, not stored. If it isn't reproducible, your pipeline is broken. |
| Permanent history | Deleting the file later does not shrink the repo — the blob stays in history forever. |

> **The principle:** commit **code and configuration**, never **derived data**. `data/raw/*.csv` is an exception here only because the sample files are tiny, fictional, and function as fixtures for the tests.

### 14.5 Removing something already committed

```bash
# Stop tracking but keep the local file
git rm -r --cached data/gold/
git commit -m "chore(repo): stop tracking generated gold layer output"
```

> ⚠️ This removes it from **future** commits only. The data is still in history. If a **credential** was committed, rotate the credential immediately — that is the only real remedy. Purging it with `git filter-repo` or BFG rewrites all history and breaks everyone's clone; do it only as a secondary cleanup after rotation.

### 14.6 Preventing secrets from being committed

```bash
# A pre-commit hook: .git/hooks/pre-commit  (chmod +x it)
#!/usr/bin/env bash
set -euo pipefail

if git diff --cached --name-only | grep -qE '\.(env|pem|key|pfx)$'; then
    echo "ERROR: attempting to commit a secret file." >&2
    exit 1
fi

if git diff --cached -U0 | grep -nEi '(password|secret|api[_-]?key|connection[_-]?string)\s*=\s*["'"'"'][^"'"'"']+'; then
    echo "ERROR: possible hardcoded credential in the staged diff." >&2
    exit 1
fi
```

Better still, adopt the `pre-commit` framework and a scanner like `gitleaks` or `detect-secrets`.

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.18.4
    hooks:
      - id: gitleaks
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.5.0
    hooks:
      - id: ruff
      - id: ruff-format
```

### 14.7 `.gitattributes` for line endings

```gitattributes
* text=auto eol=lf
*.csv  text eol=lf
*.sql  text eol=lf
*.py   text eol=lf
*.md   text eol=lf
*.sh   text eol=lf
*.parquet binary
*.png  binary
```

Combined with `git config --global core.autocrlf input`, this guarantees that files are stored with `LF` in the repository regardless of who commits from Windows. That matters because a `CRLF` in `orders.csv` puts a stray `\r` at the end of the `updated_at` column and quietly breaks timestamp parsing in Spark.

---

## 15. Dangerous commands and how to recover

Four commands cause the majority of "I lost my work" incidents. Learn exactly what each destroys.

### 15.1 ⚠️ `rm -rf`

```bash
rm -rf data/gold
```

**Destroys:** files on disk, permanently. No recycle bin. No `git` protection for untracked files.

**Recoverable?**
- If the files were **committed**: yes — `git restore` or `git checkout HEAD -- data/gold`.
- If they were **untracked**: **no**. They are gone.

**Prevent:**

```bash
ls -la data/gold                       # 1. ALWAYS look first
rm -rf "${TARGET:?TARGET is unset}"    # 2. guard variables
alias rm='rm -I'                       # 3. prompt on bulk deletes
```

**The classic catastrophe:**

```bash
TARGET_DIR=""          # oops, the variable was never set
rm -rf $TARGET_DIR/*   # expands to:  rm -rf /*
```

The `${VAR:?message}` form aborts instead:

```bash
rm -rf "${TARGET_DIR:?TARGET_DIR must be set}"/*
# bash: TARGET_DIR: TARGET_DIR must be set
```

### 15.2 ⚠️ `git reset --hard`

```bash
git reset --hard HEAD~3
```

**Destroys:** all uncommitted changes in the working tree and index, and moves the branch pointer back, orphaning the last three commits.

**Recoverable?**
- **Committed work:** yes, via `git reflog` (see §15.5) — for ~90 days.
- **Uncommitted work:** **no**, unless you had stashed it. This is the part people lose.

```bash
# Safe alternatives
git stash                       # park changes instead of destroying them
git reset --soft HEAD~1         # undo the commit, keep everything
git revert <sha>                # reverse a commit without rewriting history
```

### 15.3 ⚠️ `git clean -fd`

```bash
git clean -fd
```

**Destroys:** all **untracked** files (`-f`) and directories (`-d`). Because they are untracked, Git has no copy. There is **no recovery, not even via reflog**.

This is a very common way to lose a brand-new file you hadn't committed yet.

```bash
# ALWAYS dry-run first — -n means "show me, don't do it"
git clean -nd
```

```
Would remove data/gold/
Would remove scratch/
Would remove tests/test_scd2_dim_customer.py     <- your new test! Not yet committed!
```

That preview just saved your test file. Commit or stash it first:

```bash
git add tests/test_scd2_dim_customer.py
git clean -fd
```

| Flag | Meaning |
|---|---|
| `-n` | dry run — **always start here** |
| `-f` | force (required; Git refuses without it) |
| `-d` | include directories |
| `-x` | **also** remove ignored files (`.venv/`, `.env` — extremely destructive) |
| `-i` | interactive mode |

> `git clean -fdx` will delete your `.venv` and your `.env`. Treat it as a nuclear option.

### 15.4 ⚠️ Force push

```bash
git push --force origin main
```

**Destroys:** commits on the remote that aren't in your local branch — **including your colleagues' work**. Anyone who had pulled now has a divergent history.

**Recoverable?** Only if someone still has the old commits locally, or via the remote's reflog (GitHub support can sometimes help, but do not rely on it).

**Use `--force-with-lease` instead, always:**

```bash
git push --force-with-lease origin feature/scd2-dim-customer
```

`--force-with-lease` refuses to push if the remote has commits you haven't seen. It converts "silently destroy my colleague's work" into a safe error:

```
 ! [rejected]  feature/scd2-dim-customer -> feature/scd2-dim-customer (stale info)
error: failed to push some refs
```

**Rules:**

| Branch | Force push? |
|---|---|
| `main` / `master` / `develop` | **NEVER.** Protect it in GitHub settings. |
| A shared feature branch | Only after telling everyone working on it |
| Your own un-reviewed feature branch | Acceptable, with `--force-with-lease` |
| A branch under active review | Avoid — it breaks reviewers' "changes since last review" |

Enable branch protection on GitHub: require a PR, require status checks, block force pushes and deletions on `main`.

### 15.5 🛟 `git reflog` — your safety net

The reflog records **every movement of HEAD and of branch tips** in your local repository, including commits orphaned by `reset`, `rebase`, and `amend`. It keeps entries for ~90 days by default.

```bash
git reflog
```

```
3c7f1a9 (HEAD -> feature/scd2) HEAD@{0}: reset: moving to HEAD~3
8d2b4e7 HEAD@{1}: commit: Add as-of join for customer_key
f9a1c35 HEAD@{2}: commit: Add effective_from/effective_to to dim_customer
2e6d8b1 HEAD@{3}: commit: Add is_current flag
3c7f1a9 HEAD@{4}: checkout: moving from main to feature/scd2
8c4e1a9 HEAD@{5}: pull origin main: Fast-forward
```

#### Recovery scenario 1 — you reset away three good commits

```bash
git reset --hard HEAD~3        # 😱
git reflog                     # find where you were: 8d2b4e7 at HEAD@{1}
git reset --hard 8d2b4e7       # restored
git log --oneline -n 3
```

```
8d2b4e7 (HEAD -> feature/scd2) Add as-of join for customer_key
f9a1c35 Add effective_from/effective_to to dim_customer
2e6d8b1 Add is_current flag
```

All three commits are back.

#### Recovery scenario 2 — a rebase went wrong

```bash
git reflog
```

```
a1b2c3d HEAD@{0}: rebase (finish): returning to refs/heads/feature/scd2
...
8d2b4e7 HEAD@{6}: rebase (start): checkout main
```

```bash
# Recover the pre-rebase state
git reset --hard HEAD@{6}
# or use the automatic backup ref Git keeps
git reset --hard ORIG_HEAD
```

#### Recovery scenario 3 — you deleted a branch

```bash
git branch -D feature/scd2-dim-customer      # 😱
git reflog | grep "scd2"
```

```
8d2b4e7 HEAD@{4}: checkout: moving from feature/scd2-dim-customer to main
```

```bash
git switch -c feature/scd2-dim-customer 8d2b4e7
```

```
Switched to a new branch 'feature/scd2-dim-customer'
```

#### Recovery scenario 4 — you amended away a good commit

```bash
git commit --amend -m "oops"       # the original commit is now orphaned
git reflog
git reset --hard HEAD@{1}          # back to the original
```

#### Find orphaned commits the reflog missed

```bash
git fsck --lost-found --no-reflogs
```

```
dangling commit 7f3e9a2c1b4d5e6f8a9b0c1d2e3f4a5b6c7d8e9f
```

```bash
git show 7f3e9a2
git cherry-pick 7f3e9a2
```

### 15.6 Summary of what reflog can and cannot save

| Lost work | Recoverable via reflog? |
|---|---|
| Commits removed by `reset --hard` | ✅ yes |
| Commits orphaned by `rebase` | ✅ yes |
| A commit replaced by `--amend` | ✅ yes |
| A deleted local branch | ✅ yes |
| A `cherry-pick` or `merge` you want to undo | ✅ yes |
| **Uncommitted** working-tree changes destroyed by `reset --hard` | ❌ **no** |
| **Untracked** files destroyed by `git clean` | ❌ **no** |
| Files destroyed by `rm -rf` that were never committed | ❌ **no** |
| Remote commits destroyed by someone else's force push | ⚠️ only if someone has them locally |

> **The lesson in one line: commit early and often.** A commit is cheap, local, and reversible. Anything in a commit can be recovered for ~90 days. Anything not in a commit is one keystroke away from gone.

### 15.7 A pre-flight checklist for dangerous operations

```bash
git status                 # 1. anything uncommitted?
git stash -u               # 2. park it if so (-u includes untracked)
git branch backup/$(date +%Y%m%d-%H%M)   # 3. a cheap, named escape hatch
git reflog | head -5       # 4. note where you are
# ... now do the risky thing ...
```

---

## 16. How this maps to the project

| Skill from this guide | Where you use it in `../customer-sales-lakehouse` |
|---|---|
| `head -n 1 data/raw/*.csv` | Check schemas before writing `src/ingestion.py` |
| `wc -l data/raw/orders.csv` | Confirm the source has 8 data rows before/after ingestion |
| `grep -c "Completed"` | Sanity-check the status filter in `src/transformations.py` |
| `cut \| sort \| uniq -d` | Independently confirm order 107 is the only duplicate |
| `comm -23` on key files | Independently confirm customer 99 is the only orphan FK |
| `awk -F',' '{s+=$6}'` | Independently derive 525.00 as a cross-check on PySpark |
| `tail -f logs/pipeline.log` | Watch a pipeline run in real time |
| `grep -rn "fact_sales" src/` | Find every module that touches the fact table |
| `find . -name "*.sql"` | Inventory the `sql/` directory |
| `chmod 755 scripts/*.sh` | Make the pipeline runner executable |
| `chmod 600` + `.gitignore` | Keep `.env` and any credential file out of the repo |
| `git switch -c feature/...` | Every change starts on a branch |
| `git add -p` | Commit only the intended change, not debug prints |
| `git diff --staged` | Last check before every commit |
| Conventional commit messages | Readable history in `git log` |
| `.gitignore` for `data/gold/` | Never commit generated Parquet |
| Merge-conflict resolution | Two people editing `sql/03_reporting_queries.sql` |
| `git reflog` | Recover after a bad `reset --hard` |
| `set -euo pipefail` | Every script in `orchestration/` |
| `&&` chaining | Enforce bronze → silver → gold ordering |

### A complete daily loop

```bash
# Morning: sync
cd ~/Learn/customer-sales-lakehouse
git switch main && git pull origin main

# Start work
git switch -c fix/silver-dedup-ordering

# Inspect the data before changing code
head -n 1 data/raw/orders.csv
tail -n +2 data/raw/orders.csv | cut -d',' -f1 | sort | uniq -d

# Change code, then verify
python -m pytest tests/ -q
python notebooks/07_data_quality_checks.py 2>&1 | tee -a logs/pipeline.log | tail -3

# Commit deliberately
git status -sb
git add -p src/transformations.py
git diff --staged | grep -nE "print\(|password|secret|token"
git commit -m "fix(silver): order dedup by updated_at DESC so revisions win"

# Share
git push -u origin fix/silver-dedup-ordering
gh pr create --fill

# After merge
git switch main && git pull origin main
git branch -d fix/silver-dedup-ordering
git fetch --prune
```

---

## 17. Quick-reference cheat sheet

### Navigation and inspection

| Command | Purpose |
|---|---|
| `pwd` | print the current directory |
| `ls -lah` | list all entries, long format, human-readable sizes |
| `ls -lt` | list newest first |
| `cd path` / `cd ..` / `cd -` / `cd ~` | change directory / up / back / home |
| `tree -L 2` | show the directory tree two levels deep |
| `du -sh *` | size of each entry in the current directory |
| `df -h` | free disk space |
| `stat file` | detailed metadata for a file |
| `file data.csv` | identify a file's type and encoding |
| `namei -l path/to/file` | show permissions of every component of a path |

### Files and directories

| Command | Purpose |
|---|---|
| `mkdir -p a/b/c` | create nested directories (idempotent) |
| `touch f` | create an empty file / update its timestamp |
| `cp a b` / `cp -r dir1 dir2` | copy file / copy directory tree |
| `mv a b` | move or rename |
| `rm f` / `rm -r dir` / `rm -rf dir` | remove file / directory / force (⚠️) |
| `ln -s target link` | create a symbolic link |

### Viewing and searching

| Command | Purpose |
|---|---|
| `cat f` / `cat -n f` / `cat -A f` | print / with line numbers / show invisible chars |
| `head -n 5 f` / `tail -n 5 f` | first / last 5 lines |
| `tail -f f` | follow a growing log |
| `tail -n +2 f` | skip the header row |
| `less f` | page through interactively (`q` quits, `/` searches) |
| `wc -l f` | count lines |
| `grep "pat" f` | find matching lines |
| `grep -rn "pat" dir/` | recursive search with line numbers |
| `grep -c "pat" f` | count matching lines |
| `grep -v "pat" f` | invert the match |
| `grep -i "pat" f` | case-insensitive |
| `grep -E "a\|b" f` | extended regex alternation |
| `find . -name "*.csv"` | find files by name |
| `find . -type d -name "x" -exec rm -rf {} +` | find and act (⚠️ preview first) |

### Text processing

| Command | Purpose |
|---|---|
| `cut -d',' -f1,3 f` | extract CSV fields 1 and 3 |
| `sort f` / `sort -n` / `sort -u` | sort / numerically / unique |
| `uniq -c` / `uniq -d` | count duplicates / show only duplicates |
| `awk -F',' '{s+=$6} END{print s}' f` | sum a CSV column |
| `sed 's/old/new/g' f` | substitute text in a stream |
| `tr -d '\r' < f > out` | strip carriage returns |
| `comm -23 a b` | lines in sorted file `a` only |
| `diff a b` | show differences between two files |

### Pipes and redirection

| Syntax | Purpose |
|---|---|
| `a \| b` | pipe a's stdout into b's stdin |
| `cmd > f` | redirect stdout, overwriting |
| `cmd >> f` | redirect stdout, appending |
| `cmd 2> f` | redirect stderr |
| `cmd > f 2>&1` | redirect both to one file |
| `cmd \| tee f` | write to file and screen |
| `cmd \| tee -a f` | append to file and screen |
| `cmd < f` | read stdin from a file |
| `a && b` / `a \|\| b` / `a ; b` | on success / on failure / regardless |
| `echo $?` | exit code of the last command |

### Permissions

| Command | Purpose |
|---|---|
| `ls -l` | view permissions |
| `chmod 644 f` | `rw-r--r--` — normal data file |
| `chmod 755 f` | `rwxr-xr-x` — script or directory |
| `chmod 600 f` | `rw-------` — secrets |
| `chmod +x f` | make executable |
| `chmod -R u=rwX,go=rX dir/` | recursive, `X` = execute on directories only |
| `chown user:group f` | change owner and group |
| `chgrp group f` | change group only |
| `umask` | show the default permission mask |
| `whoami` / `id` | current user / full identity |

### Git — setup and inspection

| Command | Purpose |
|---|---|
| `git config --global user.name "X"` | set your name |
| `git init` | create a repository |
| `git clone <url>` | copy a remote repository |
| `git status -sb` | short status with branch info |
| `git diff` | unstaged changes |
| `git diff --staged` | staged changes (review before committing) |
| `git log --oneline --graph --decorate --all` | visual history |
| `git show <sha>` | details of one commit |
| `git blame -L 40,46 f` | who changed these lines, and when |
| `git remote -v` | list remotes |

### Git — making changes

| Command | Purpose |
|---|---|
| `git add f` / `git add -A` / `git add -p` | stage file / everything / interactively |
| `git restore --staged f` | unstage, keep edits |
| `git restore f` | discard working-tree edits (⚠️) |
| `git commit -m "msg"` | commit with a message |
| `git commit --amend` | rewrite the last commit (⚠️ if pushed) |
| `git stash` / `git stash pop` | park / restore work in progress |
| `git revert <sha>` | safely reverse a commit |
| `git reset --soft HEAD~1` | undo the commit, keep changes staged |
| `git reset --hard HEAD~1` | undo and **destroy** changes (⚠️) |

### Git — branching and sharing

| Command | Purpose |
|---|---|
| `git branch -vv` | list branches with upstreams |
| `git switch -c feature/x` | create and switch to a branch |
| `git switch main` / `git switch -` | switch / switch back |
| `git merge feature/x` | merge a branch in |
| `git merge --abort` | abandon a conflicted merge |
| `git rebase main` | replay your commits on top of main |
| `git rebase -i main` | interactively clean up commits |
| `git rebase --abort` / `--continue` | abandon / resume a rebase |
| `git fetch origin` | download without changing your tree (always safe) |
| `git pull origin main` | fetch + merge |
| `git push -u origin feature/x` | push and set upstream |
| `git push --force-with-lease` | safe force push (⚠️ never on `main`) |
| `git branch -d feature/x` | delete a merged branch |
| `git push origin --delete feature/x` | delete the remote branch |
| `git fetch --prune` | remove stale remote-tracking refs |

### Git — recovery

| Command | Purpose |
|---|---|
| `git reflog` | 🛟 history of every HEAD movement |
| `git reset --hard HEAD@{1}` | jump back to a reflog entry |
| `git reset --hard ORIG_HEAD` | undo the last merge or rebase |
| `git switch -c recovered <sha>` | rescue an orphaned commit onto a branch |
| `git fsck --lost-found` | find dangling commits |
| `git clean -nd` | 🛟 **dry run** before any `git clean` |
| `git clean -fd` | delete untracked files (⚠️ unrecoverable) |

### Danger meter

| Command | Risk | Recoverable? |
|---|---|---|
| `git fetch` | 🟢 none | n/a |
| `git status`, `git log`, `git diff` | 🟢 none | n/a |
| `git stash` | 🟢 low | yes (`stash list`) |
| `git commit --amend` (unpushed) | 🟡 medium | yes (reflog) |
| `git rebase` | 🟡 medium | yes (reflog) |
| `git reset --hard` | 🔴 high | commits yes, uncommitted **no** |
| `git clean -fd` | 🔴 high | **no** |
| `git push --force` | 🔴 high | only from someone's local copy |
| `rm -rf` | 🔴 critical | only if committed |

---

## 18. Exercises

Do these in a real shell (WSL or Git Bash) against `../customer-sales-lakehouse`. No answers are provided.

1. From inside `data-engineering-learning/`, write a **single** command using only relative paths that prints the first line of all three raw CSVs in the sibling project. Then write the equivalent PowerShell command and explain one behavioural difference between them.

2. Using only `tail`, `cut`, `sort`, `uniq` and `wc`, produce a command that prints the number of **distinct** `order_id` values in `orders.csv`. Then produce a second command that prints only the IDs appearing more than once. State what you expect before you run them.

3. Build a one-line pipeline that computes total recognised revenue from `orders.csv` — Completed only, de-duplicated, excluding `customer_id = 99` — and prints it to two decimal places. Verify it returns `525.00`. Then deliberately break one stage (remove the `uniq`) and explain the number you get and why.

4. Write a `grep` command that finds every row in `orders.csv` with a missing `amount`, and a second that finds every row whose `customer_id` is not one of 1, 2, 3, 4. Explain why the second is fragile as a general data-quality check and what you would use instead.

5. Write a bash script `scripts/validate_raw.sh` that exits `0` only if **all** of the following hold: `orders.csv` has exactly 9 lines, exactly one duplicate `order_id`, exactly one row with a missing amount, and exactly one orphan `customer_id`. Use `set -euo pipefail`. Make it executable with the correct `chmod` and explain your choice of mode.

6. Create `scratch/perms/` containing one `.sh` file and one `.csv` file. Using a **single recursive** `chmod` command, make directories `755`, the shell script `755`, and the CSV `644`. Explain precisely why capital `X` is necessary and what would go wrong with lowercase `x`.

7. Run `namei -l` on `data/raw/orders.csv`. Write out what each permission string in the output means, and describe a scenario where the file itself is `644` and owned by you, yet you still get "Permission denied".

8. Initialise a Git repository in a scratch folder, create three commits, then use `git reset --hard HEAD~2`. Recover all three commits using only `git reflog`. Write down the exact sequence of commands you used.

9. Create two branches from the same commit that both edit the **same lines** of `sql/03_reporting_queries.sql` in different ways. Merge one, then merge the other to trigger a conflict. Resolve it so that both queries survive, verify with `grep` that no conflict markers remain, and commit the merge with a message explaining the resolution.

10. Write a `.gitignore` for a variant of this project that additionally produces `data/exports/*.xlsx` and `reports/*.html`, but where `reports/template.html` **must** be tracked. Verify your negation pattern works using `git status --ignored` and `git check-ignore -v <path>`.

11. Stage a file, then use `git add -p` on a second file to stage only **one** of two separate hunks. Run `git diff` and `git diff --staged` and explain, in your own words, exactly which of the four Git "places" each command is comparing.

12. Simulate a lost file: create `tests/test_new.py`, do **not** commit it, run `git clean -nd`, then `git clean -fd`. Confirm it is unrecoverable, then write down the two different commands you could have run beforehand to protect it.

13. Write three commit messages — one `feat`, one `fix`, one `docs` — for real changes you would make to this project. Each must follow all seven rules in §13.1 and include a body explaining *why*. Then write the deliberately bad version of each and annotate what is wrong.

14. Rebase a two-commit feature branch onto a `main` that has moved forward by one commit. Capture `git log --oneline --graph --all` before and after. Explain why the commit SHAs changed and what would have happened if a colleague had already pulled that branch.

15. Set up branch protection reasoning on paper: list the four Git/GitHub settings you would enable on `main` for this project, and for each one, name a specific incident from §15 that it prevents.

---

**You now have the two foundations.** `02_data_modelling_deep_dive.md` taught you what to build; this guide taught you how to build it safely and share it. Next: `04_etl_elt_and_quality.md` for the pipeline patterns, then `08_dbt_orchestration_monitoring.md` for running it all on a schedule.


