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

### KDB+ Initialization


```q