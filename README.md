# Auto Trader Web Scraper
## Overview
The Auto Trader Web Scraper project automates the extraction of vehicle listings from AutoTrader.com. Powered by Python and leveraging BeautifulSoup for parsing, this scraper retrieves detailed information about vehicles based on predefined search queries.

## Key Features
- **Configuration File Integration**: Utilizes a config.txt file containing search queries to dynamically update URLs and fetch relevant page data.
- **Data Extraction:** Parses vehicle details such as make, model, year, price, mileage, and location from the HTML content.
- **CSV Output:** Outputs scraped data into a structured CSV format using Pandas, facilitating easy analysis and further processing.
- **Automated Scraping:** Automates the scraping process to gather comprehensive data across multiple searches without manual intervention.
- 
## Requirements
- Python 3.x
- BeautifulSoup (bs4)
- Requests
- Pandas
- 
## Getting Started
Clone the Repository:

```bash
https://github.com/Muneeb1030/Autotrader.git
cd auto-trader-scraper
```
## Install Dependencies:

```bash
pip install beautifulsoup4 requests pandas
```
## Run the Scraper:

```bash
python auto_trader.py
```

## Additional Information
- **Customization:** Modify config.txt to add or change search queries as per your requirements.
- **GitHub Repository:** Explore and contribute to the project on GitHub.
  
## Disclaimer: 
This scraper should be used responsibly and in accordance with AutoTrader.com's terms of service.

## Author
- M Muneeb ur Rehman


Feel free to contribute and enhance the capabilities of this Auto Trader scraper. Happy scraping! 🚗💻
