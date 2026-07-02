Setup Instructions

0. Download the Map Data

The GIS map data is too large to host on GitHub. Before running the project:

i. Download the shapefile data here: https://download.geofabrik.de/europe/greece-latest-free.shp.zip (via https://download.geofabrik.de/europe/greece.html)

ii. Extract the .zip file and place the contents inside the data folder of this project.

iii. Note: This project originally used version 251102. Since the link provides the latest version, the filename will be slightly different. You may need ot rename your downloaded files to match greece-251102-free or quickly update the filepath inside main.py (line 26).

1. Install System Dependencies (Linux Only)
    If you are using Linux, you will need to install Tkinter. (Windows and macOS users generally have this installed with Python by default).

        Linux: sudo apt-get install python3-tk

2. Create a Virtual Environment
    Open your terminal or command prompt in the project directory and run:

        Windows: python -m venv .venv

        macOS/Linux: python3 -m venv .venv

3. Activate the Virtual Environment

        Windows (Command Prompt): .venv\Scripts\activate.bat

        Windows (PowerShell): .venv\Scripts\Activate.ps1

        macOS/Linux: source .venv/bin/activate

4. Install Python Dependencies
    Once your environment is active, install the required packages:
    pip install -r requirements.txt
    (Note: If you plan to run the visualization scripts, you will also need matplotlib).

5. Run the Server

        Start the FastAPI server using Uvicorn: uvicorn main:app --reload

        Open your browser and go to http://127.0.0.1:8000/ or http://localhost:8000/.
