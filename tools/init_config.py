import argparse
import importlib.util
from pathlib import Path

from ruamel.yaml import YAML

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-u", "--username", type=str)
    parser.add_argument("-pw", "--password", type=str)
    parser.add_argument(
        "--host",
        type=str,
        default="localhost",
        description="host of both kdb and sql, will be overwritten if sqlhost or kdbhost is specified",
    )
    parser.add_argument("--sqlhost", type=str, default="", description="host of sql")
    parser.add_argument("--sqldb", type=str, default="trade_data", description="database name of sql")
    parser.add_argument("--kdbhost", type=str, default="", description="host of kdb")
    parser.add_argument("--sqlport", type=str, default="5432", description="port of sql")
    parser.add_argument("--kdbport", type=str, default="5000", description="port of kdb")

    args = parser.parse_args()

    kdb_host = args.host if args.kdbhost == "" else args.kdbhost
    sql_host = args.host if args.sqlhost == "" else args.sqlhost

    sql_protocol = "postgresql" if importlib.util.find_spec("psycopg2") is None else "postgresql+psycopg2"

    config = {
        "username": args.username,
        "password": args.password,
        "kdbhost": kdb_host,
        "kdbport": args.kdbport,
        "sqlconnstr": f"{sql_protocol}://{args.username}:{args.password}@{sql_host}:{args.sqlport}/{args.sqldb}",
    }

    yaml = YAML(typ="safe")
    config_folder = Path("~/.tradedbmgr").expanduser()
    config_folder.mkdir(exist_ok=True)
    with open(config_folder.joinpath("config.yaml"), "w") as f:
        yaml.dump(config, f)
