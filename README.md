# Delta Hedging Automation Platform

## Overview

The Delta Hedging Automation Platform is a sophisticated financial tool designed to manage and hedge option positions dynamically using the Black-Scholes option pricing model. This project provides an end-to-end solution for creating, monitoring, and hedging financial derivatives with intelligent risk management.

![Project Architecture](https://img.shields.io/badge/Architecture-Flask%20%7C%20JavaScript%20%7C%20Axios-blue)
![Python Version](https://img.shields.io/badge/Python-3.8%2B-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

## 🚀 Key Features

- **Automated Option Position Management**
- **Dynamic Delta Hedging**
- **Real-time Market Data Simulation**
- **Comprehensive Risk Analytics**
- **Flexible Hedging Strategies**

## 📦 Prerequisites

- Python 3.8+
- pip (Python Package Manager)
- Virtual Environment (recommended)

## 🔧 Installation

1.Clone the repository:

```bash
git clone https://github.com/yourusername/delta-hedging-platform.git
cd delta-hedging-platform
```

2.Create a virtual environment:

```bash
python -m venv venv
source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
```

3.Install dependencies:

```bash
pip install -r requirements.txt
```

4.Set up environment variables (optional):

```bash
# Create a .env file in the project root
IG_API_KEY=your_ig_api_key
IG_USERNAME=your_username
IG_PASSWORD=your_password
IG_ACC_TYPE=DEMO/LIVE
```

## 🎬 Running the Application

### Backend (Flask Server)

```bash
# Run the Flask development server
python app.py
```

### Frontend (Web Dashboard)

Open the `index.html` file in a modern web browser.

## 🧰 Technologies Used

- **Backend**:

  - Flask
  - NumPy
  - SciPy
  - Requests

- **Frontend**:
  - Vanilla JavaScript
  - Axios
  - Tailwind CSS

- **Financial Modeling**:
  - Black-Scholes Option Pricing Model
  - Delta Hedging Algorithm

## 🔬 How It Works

### Option Position Management

1. Create option positions with strike price, type, and expiration
2. Track real-time market data
3. Calculate option delta
4. Automatically hedge positions based on predefined risk thresholds

### Delta Hedging Strategy

- Calculates option sensitivity (delta)
- Dynamically adjusts hedge positions
- Manages risk by keeping portfolio delta-neutral

## 📊 Key Components

- **IGClient**: Simulated market data and trading interface
- **OptionCalculator**: Black-Scholes option pricing and delta calculation
- **DeltaHedger**: Core hedging logic and position management
- **MockMarketData**: Realistic price simulation

## 🔍 Example Usage


# Create a new option position
position_data = {
    "epic": "CS.D.EURUSD.TODAY.IP",
    "strike": 1.2000,
    "option_type": "CALL",
    "premium": 50,
    "contracts": 1,
    "time_to_expiry": 0.25
}
position_id = hedger.create_position(position_data)
``
## Deployment to aws ec2

1. Create an EC2 instance
2. Connect to the instance using SSH
3. Install Git and clone the repository
4. Install Python and the required packages
5. Run the Flask server
6. Access the application using the public IP address

`


## 🛡️ Error Handling

- Comprehensive logging
- Graceful error management
- Fallback to mock data during API failures

## 🔜 Roadmap

- [ ] Support for multiple option types
- [ ] Advanced risk metrics
- [ ] Machine learning-based prediction
- [ ] Real broker API integration

## 🤝 Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## ⚖️ License

Distributed under the MIT License. See `LICENSE` for more information.

## 📞 Contact

Nirajan Karki - <kneeraazon@gmail.com>

---

**Disclaimer**: This is a simulation tool. Always consult financial professionals before making investment decisions.
