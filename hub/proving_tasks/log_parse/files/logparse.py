def count_errors(lines):
    """How many ERROR lines there are of each error code.

    A line looks like `2026-10-01 12:00:01 ERROR E101 something went wrong`: a date, a time, the level, then - for
    ERROR lines - the code: an «E» and digits. Fields are separated by any run of spaces or tabs; a line may end with
    \\n or \\r\\n. Count only lines whose level is exactly ERROR and whose fourth field is such a code; skip every other
    line (blank ones, WARN and INFO, «error» in lower case, an ERROR without a code or with a code like «X1»).

    Returns {code: count}, e.g. {"E101": 2, "E7": 1}; {} when there is none.
    """
    raise NotImplementedError
