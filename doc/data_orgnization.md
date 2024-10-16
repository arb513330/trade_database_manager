# DATA ORGANIZATION

This file specifys the data organization in the database.

### Instrument Metadata

The common information of all instruments are stored in the `instruments` table. The columns in the table are show in below table

| Column Name   | Data Type      | Description                        |
|---------------|----------------|------------------------------------|
| ticker        | str            | Trade Symbol at exchange           |
| exchange      | Enum[Exchange] | Primary exchange / Trade route etc |
| trading_code  | str            | Trading code at exchange           |
| name          | str            | Instrument name                    |
| inst_type     | Enum[InstType] | Instrument type                    |
| currency      | str            | Quote currency                     |
| timezone      | str            | Timezone                           |
| tick_size     | float          | Instrument tick size               |
| lot_size      | float          | Lot size                           |
| min_lots      | float          | Minimum lots to trade              |
| market_tplus  | int            | T+? can sell                       |
| listed_date   | datetime       | Listed date                        |
| delisted_date | datetime       | Delisted date                      |

<!--| stop_trading_date | datetime       | Date the contract removed from trading (Can be different of delisting date for some instruments) |-->

The type-specific information are stored in tables named `instruments_<type>` where `<type>` is the type of the instrument (in lower case).
Below table shows the columns in the type-specific tables (besides the symbol | exchange | currency column):

- Stock (STK)
    
    | Columns      | Data Type | Instrument Types which requires the column |
    |--------------|-----------|--------------------------------------------|
    | country      | str       | Country                                    |
    | state        | str       | State/Province                             |                             
    | board_type   | str       | Mainboard etc.                             |                            
    | issue_price  | float     | Stock issuing price                        |                        

- Future (FUT)
- LOF & ETF
   
    | Columns      | Data Type | Instrument Types which requires the column |
    |--------------|-----------|--------------------------------------------|
    | country      | str       | Country                                    |
-    
