<table border=1 cellpadding=10>
<tr>
<td style="color: red;">

#### \*\*\* IMPORTANT NOTICE \*\*\*

<p style="color: red">This package is <b>not</b> in a usable stage. It is only uploaded for convenience of developing and testing.</p>

</td></tr></table>



### Postgreslq Initialization

```sql
CREATE DATABASE trade_data;
CREATE USER tradedbadmin WITH PASSWORD 'trade_password';
GRANT ALL PRIVILEGES ON DATABASE trade_data TO tradedbadmin;
\connect trade_data;
GRANT ALL ON SCHEMA public TO tradedbadmin;
```

Add the following lines in the `pg_hba.conf` file to allow password authentication for the `tradedbadmin` role.

```
# trade database
host    all            tradedbadmin     0.0.0.0/0               scram-sha-256
host    all            tradedbadmin     ::/0                    scram-sha-256
```

### MetaData Initialization

Allowed instruments types are given in "Instrument Types" section of [meta_enumerations.md](doc/data_organization/meta_enumerations.md).

### Data Initialization for Instrument Type(s)
```python
from trade_database_manager.manager import MetadataSql

metadatalib = MetadataSql()
metadatalib.initialize(for_inst_types="CB")
```

This will try to create two tables, `instruments` and `instruments_cb` in the database if not yet exists. The `instruments` table will store the common information of all instruments, and the `instruments_cb` table will store the type-specific information of the instruments of type `CB`.

The table fields are listed in the [data_organization.md](doc/data_organization.md) file.




