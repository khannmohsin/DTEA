
import re
def get_enode():
    enode_file = "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/data/enode.txt"
    with open(enode_file, "r") as file:
        enode_url = file.read().strip()
    # response = requests.post(self.besu_rpc_url, json=payload, headers={"Content-Type": "application/json"})
    # data = response.json()
    # enode_url = data.get("result", {}).get("enode", "")
    
    if enode_url:
        
        match = re.match(r"enode://([a-fA-F0-9]+)@([\d\.]+):(\d+)", enode_url)
        if match:
            print(enode_url)
            return enode_url  
    return None



    
get_enode()