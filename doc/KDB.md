### Installation

## Download
Visit [KDB](https://kx.com/kdb-personal-edition-download/)， fill in your information and wait for email from KX <downloads@marketing.kx.com> for download instruction.
Download the latest version of KDB+ for your operating system. It should be a zip file contains a folder which contains the executable file `q`.
Also, a license file `kc.lic` and its base64 encoding is also available. One need to download the file. 

## Locate the file and Setup Environment Variables
 - Unzip the zip file `[x]64.zip`, It contains a `q.k` file and a `[x]64.zip` file where `[x]` is different by target system (`w` for Windows, `l` for Linux, `m` for MacOS),
 - Place `q` in a desired location, e.g. `/opt/l64/q` for Linux, `C:\l64\q` for Windows, then add the path to the environment variable `PATH`;
 - Place `kc.lic` in the same folder as `q`, then add the path to the environment variable `QLIC`;
 - Decide where you want to store the data, and put `q.k` file in that folder and add the path to the environment variable `QHOME`.

## Test the Installation
 - Open a terminal, type `q` and press enter, you should see a welcome message and a `q)` prompt;
 - Type `2+3` and press enter, you should see `5` as the result;
 - Type `.z.K` and press enter, you should see a date and time string like `2020.12.31T23:59:59.999`.

## Register `q` as a System Service
 - Create an environment variable file `q.env` in the same folder as `q` and `kc.lic`, and add the following lines:
   ```bash
   printf "QHOME=/data0/kdbdata/q\nQLIC=/opt/l64/\n" | sudo tee /opt/l64/kdbenv
   ```
- Create a service file `q.service` in `/etc/systemd/system/` and add the following lines:
  ```bash
  sudo vim /etc/systemd/system/q.service
  ```
  ```ini
  [Unit]
  Description=KDB+ Q Service
  After=network.target
   
  [Service]
  EnvironmentFile=/opt/l64/kdbenv
  ExecStart=/opt/l64/q -p 5001
  User=youruser
  Group=yourgroup
  Restart=always
  RestartSec=1
   
  [Install]
  WantedBy=multi-user.target
  ```




