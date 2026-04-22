"""
Package Management Configuration for Glamour Bot
Manages package creation, pricing, discounts, and feature availability
"""

import json
import os
from typing import Dict, List, Optional

# Default packages configuration
DEFAULT_PACKAGES = {
    "glamfee": {
        "name": "GlamFee",
        "display_name": "GlamFee",
        "price": 14000,
        "original_price": 20000,
        "currency_naira": 14000,
        "currency_euro": 7,
        "emoji": "💎",
        "is_available_for_new_users": True,
        "is_available_for_registered_users": True,
        "is_premium": False,
        "description": "Start your GLAMOUR journey with GlamFee"
    }
}

PREMIUM_PACKAGES = {
    "glampremium": {
        "name": "GlamPremium",
        "display_name": "GlamPremium",
        "price": 35000,
        "original_price": 50000,
        "currency_naira": 35000,
        "currency_euro": 18,
        "emoji": "👑",
        "is_available_for_new_users": False,  # Deactivated by default
        "is_available_for_registered_users": False,  # Deactivated by default
        "is_premium": True,
        "description": "Upgrade to GlamPremium for exclusive features"
    }
}

# Features that distinguish basic from premium users
PREMIUM_FEATURES = {
    "priority_support": True,  # Premium users get priority support
    "bonus_earning_rate": 1.5,  # Premium users earn 1.5x more
    "exclusive_tasks": True,  # Premium-only tasks
    "vip_group_access": True,  # Access to exclusive VIP group
    "monthly_cash_bonus": 5000,  # Monthly bonus in Naira
    "referral_bonus_multiplier": 2.0,  # 2x referral rewards
    "advanced_analytics": True,  # Detailed earning analytics
    "withdrawal_fee_waived": True,  # No withdrawal fees
}

CONFIG_FILE = "glamour_packages.json"


class PackageManager:
    """Manages package configuration and operations"""
    
    def __init__(self):
        self.packages = self._load_config()
        self.discount_enabled = False
        self.premium_enabled = False
    
    def _load_config(self) -> Dict:
        """Load packages from config file or create default"""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
                    self.discount_enabled = config.get('discount_enabled', False)
                    self.premium_enabled = config.get('premium_enabled', False)
                    return config.get('packages', DEFAULT_PACKAGES)
            except Exception as e:
                print(f"Error loading config: {e}")
                return DEFAULT_PACKAGES
        return DEFAULT_PACKAGES
    
    def _save_config(self):
        """Save packages to config file"""
        try:
            config = {
                'packages': self.packages,
                'discount_enabled': self.discount_enabled,
                'premium_enabled': self.premium_enabled
            }
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=2)
            return True
        except Exception as e:
            print(f"Error saving config: {e}")
            return False
    
    def get_available_packages(self, for_new_user: bool = True) -> Dict:
        """Get available packages based on user type"""
        available = {}
        for pkg_id, pkg_data in self.packages.items():
            if for_new_user:
                if pkg_data.get('is_available_for_new_users'):
                    available[pkg_id] = pkg_data
            else:
                if pkg_data.get('is_available_for_registered_users'):
                    available[pkg_id] = pkg_data
        return available
    
    def add_package(self, package_id: str, package_data: Dict) -> bool:
        """Add a new package"""
        if package_id in self.packages:
            return False
        self.packages[package_id] = package_data
        return self._save_config()
    
    def remove_package(self, package_id: str) -> bool:
        """Remove a package"""
        if package_id not in self.packages:
            return False
        del self.packages[package_id]
        return self._save_config()
    
    def update_package(self, package_id: str, updates: Dict) -> bool:
        """Update package details"""
        if package_id not in self.packages:
            return False
        self.packages[package_id].update(updates)
        return self._save_config()
    
    def set_discount(self, original_price_dict: Dict) -> bool:
        """Enable discount and set prices"""
        try:
            for package_id, original_price in original_price_dict.items():
                if package_id in self.packages:
                    self.packages[package_id]['original_price'] = original_price
            self.discount_enabled = True
            return self._save_config()
        except Exception as e:
            print(f"Error setting discount: {e}")
            return False
    
    def remove_discount(self) -> bool:
        """Disable discount"""
        try:
            for package_id in self.packages:
                # Set original price to match current price when discount removed
                self.packages[package_id]['original_price'] = self.packages[package_id]['price']
            self.discount_enabled = False
            return self._save_config()
        except Exception as e:
            print(f"Error removing discount: {e}")
            return False
    
    def activate_premium(self, available_for_new: bool = False) -> bool:
        """Activate premium package availability"""
        try:
            if 'glampremium' not in self.packages:
                self.packages['glampremium'] = PREMIUM_PACKAGES['glampremium'].copy()
            
            self.packages['glampremium']['is_available_for_new_users'] = available_for_new
            self.packages['glampremium']['is_available_for_registered_users'] = True
            self.premium_enabled = True
            return self._save_config()
        except Exception as e:
            print(f"Error activating premium: {e}")
            return False
    
    def deactivate_premium(self) -> bool:
        """Deactivate premium package availability"""
        try:
            if 'glampremium' in self.packages:
                self.packages['glampremium']['is_available_for_new_users'] = False
                self.packages['glampremium']['is_available_for_registered_users'] = False
            self.premium_enabled = False
            return self._save_config()
        except Exception as e:
            print(f"Error deactivating premium: {e}")
            return False
    
    def rename_package(self, package_id: str, new_name: str, new_display_name: str) -> bool:
        """Rename a package"""
        if package_id not in self.packages:
            return False
        self.packages[package_id]['name'] = new_name
        self.packages[package_id]['display_name'] = new_display_name
        return self._save_config()
    
    def set_package_price(self, package_id: str, price: int, currency_euro: float) -> bool:
        """Set package price"""
        if package_id not in self.packages:
            return False
        self.packages[package_id]['price'] = price
        self.packages[package_id]['currency_naira'] = price
        self.packages[package_id]['currency_euro'] = currency_euro
        return self._save_config()
    
    def get_package(self, package_id: str) -> Optional[Dict]:
        """Get specific package details"""
        return self.packages.get(package_id)
    
    def list_all_packages(self) -> Dict:
        """List all packages with their status"""
        return self.packages
    
    def get_premium_features_for_user(self, is_premium_user: bool) -> Dict:
        """Get features available for user based on premium status"""
        if is_premium_user:
            return PREMIUM_FEATURES
        return {key: False for key in PREMIUM_FEATURES.keys()}


# Global instance
package_manager = PackageManager()
