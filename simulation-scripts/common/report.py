"""Result reporting shared by the experiments: colored progress prints + an
append-mode CSV writer (header written once, row-level flush so partial sweeps
survive interruption)."""
import csv
import os


def print_info(m):    print(f"\033[94m[INFO] {m}\033[0m", flush=True)
def print_success(m): print(f"\033[92m[SUCCESS] {m}\033[0m", flush=True)
def print_warning(m): print(f"\033[93m[WARNING] {m}\033[0m", flush=True)
def print_error(m):   print(f"\033[91m[ERROR] {m}\033[0m", flush=True)


class CsvAppender:
    """Append rows to a CSV, writing the header only if the file is new.

    with CsvAppender(path, fields) as out:
        out.write({...})   # flushed immediately
    """

    def __init__(self, path, fields):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_header = not os.path.exists(path)
        self._f = open(path, "a", newline="")
        self._w = csv.DictWriter(self._f, fieldnames=fields)
        if write_header:
            self._w.writeheader()

    def write(self, row):
        self._w.writerow(row)
        self._f.flush()

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
