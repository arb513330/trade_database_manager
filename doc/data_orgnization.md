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
| listed_date   | date           | Listed date                        |
| delisted_date | date           | Delisted date                      |

<!--| stop_trading_date | datetime       | Date the contract removed from trading (Can be different of delisting date for some instruments) |-->

The type-specific information are stored in tables named `instruments_<type>` where `<type>` is the type of the instrument (in lower case).
Below table shows the columns in the type-specific tables (besides the symbol | exchange | currency column):

- Stock (STK)
    
    | Columns     | Data Type | Description         |
    |-------------|-----------|---------------------|
    | country     | str       | Country             |
    | state       | str       | State/Province      |                             
    | board_type  | str       | Mainboard etc.      |                            
    | issue_price | float     | Stock issuing price |                        

- Future (FUT)

    | Columns             | Data Type          | Description                               |
    |---------------------|--------------------|-------------------------------------------| 
    | contract_unit       | str                | Contract unit                             | 
    | contract_multipler  | float              | Multiplier per lot                        | 
    | expiry_time         | datetime           | Contract expirison                        |
    | delivery_date       | date               | Delivery date                             | 
    | settlement_method   | Enum[SettleMethod] | Settlement method                         | 
    | underlying_code     | str                | Underlying for contract                   |
    | underlying_exchange | str                | Underlying trading exchange (if tradable) |
    | underlying_type     | Enum[InstType]     | Underlying type, stock, commodity, etc.   |
    | sector              | str                | Sector of underlying                      |
    | margin_method       | str                | method to calculate margin                |

- LOF & ETF
   
    | Columns | Data Type | Description |
    |---------|-----------|-------------|
    | country | str       | Country     |

- Index (IDX)
  There is no specifiec column for index.

- Convertable Bond (CB)
    
    | Columns               | Data Type | Description                                       |
    |-----------------------|-----------|---------------------------------------------------|
    | country               | str       | Country                                           |
    | state                 | str       | State/Province                                    |
    | stock_code            | float     | Underlying stock code                             |     
    | stock_exchange        | float     | Underlying stock exchange                         |
    | maturity_date         | datetime  | Bond maturity date                                |
    | issue_price           | float     | Bond issuing price                                |
    | total_issue_size      | float     | Total issue size                                  |
    | par_value             | float     | Bond par value                                    |
    | redemption_price      | float     | Price that issue pays back at expiry / redemption |
    | conversion_start_date | datetime  | Period start date for convertion                  |
    | conversion_end_date   | datetime  | Period end date for convertion                    |
    | callback_terms        | str       | Terms for callback, i.e., issuer call back        |
    | callback_type         | str       | Type of callback method                           |
    | putback_terms         | str       | Terms for investor to sell back bonds to issuer   |
    | putback_type          | str       | Type of callback method                           |
    | adjust_terms          | str       | Terms of adjust convertion price                  |
    | adjust_type           | str       | Type of callback method                           |