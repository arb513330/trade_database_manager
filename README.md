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

#### get license
Request a 64-bit license from [Kx Systems](https://kx.com/kdb-insights-personal-edition-license-download). Fill the form
then the system will send an email with the license file and a base64 key (Either can be used to license the product).

#### Server side (Linux system)
1. Put the executable `q` and license in desired folder, say `/opt/l64` directory.
2. Create a folder to store the data, say `/opt/data` directory.
3. Set the environment variable `QLIC` to the directory where the license file is stored. And `QHOME` to the data directory.
4. Run the `q` executable with the following command:
    ```bash
    $ q -p 5000 -s 1 -w 100 -t 1000 -T 1000 -U /opt/l64/trade.q
    ```
   - `-p 5000`: This sets the port number for the kdb+ process. In this case, the port number is 5000.

   - `-s 1`: This sets the number of secondary threads. In this case, it's set to 1.

   - `-w 100`: This sets the workspace heap size. In this case, it's set to 100 MB.

   - `-t 1000`: This sets the timer interval in milliseconds. In this case, it's set to 1000 milliseconds, or 1 second.

   - `-T 1000`: This sets the timeout in seconds for client queries. In this case, it's set to 1000 seconds.

   - `-U /opt/l64/trade.q`: This sets the access control list file. In this case, the file is located at `/opt/l64/trade.q`. This file contains a list of usernames and passwords for clients that are allowed to connect to the kdb+ process.
