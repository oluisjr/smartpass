from dataclasses import dataclass 

@dataclass 
class WalletPassData: 
    id: str 
    smartpass_id: str 
    name: str 
    company: str 
    event: str 
    qr_token: str 
    
@property 
def smartpass(self) -> str: 
    return self.smartpass_id