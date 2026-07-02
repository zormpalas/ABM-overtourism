// Create map centered on Patra
var map = L.map('map').setView([38.2466, 21.7346], 13);

// Add background tiles (OpenStreetMap)
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
}).addTo(map);

// Separate layer groups
var residentsLayer = L.layerGroup().addTo(map);
var touristsLayer = L.layerGroup().addTo(map);
var hotelsLayer = L.layerGroup().addTo(map);
var poisLayer = L.layerGroup().addTo(map);
var heatmapLayer = L.layerGroup().addTo(map);

// Add checkbox control for the layers
var overlays = {
    "Residents": residentsLayer,
    "Tourists": touristsLayer,
    "Hotels": hotelsLayer,
    "POIs": poisLayer,
    "Heatmap": heatmapLayer
};
L.control.layers(null, overlays).addTo(map);

// Custom icons for hotels
var hotelIcon = L.icon({
    iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-blue.png',
    shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
    iconSize: [25, 41],
    iconAnchor: [12, 41],
    popupAnchor: [1, -34],
    shadowSize: [41, 41]
});

// Custom icons for POIs (different shades of green)
var poiIconTierA = L.icon({
    iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-green.png',
    shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
    iconSize: [20, 33],
    iconAnchor: [10, 33],
    popupAnchor: [1, -28],
    shadowSize: [33, 33]
});

var poiIconTierB = L.icon({
    iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-yellow.png',
    shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
    iconSize: [18, 30],
    iconAnchor: [9, 30],
    popupAnchor: [1, -25],
    shadowSize: [30, 30]
});

var poiIconTierC = L.icon({
    iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-orange.png',
    shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
    iconSize: [16, 26],
    iconAnchor: [8, 26],
    popupAnchor: [1, -22],
    shadowSize: [26, 26]
});

// Store markers
var hotelMarkers = [];
var poiMarkers = [];

// DRAW hotels - called once
function drawHotels(hotels) {
    // Clear existing hotel markers
    hotelsLayer.clearLayers();
    hotelMarkers = [];

    // Draw hotels
    hotels.forEach(hotel => {
        let marker = L.marker([hotel.y, hotel.x], {icon: hotelIcon});
        marker.bindPopup(`
            <b>🏨 ${hotel.name}</b><br>
            Type: ${hotel.type}<br>
            Lon: ${hotel.x.toFixed(5)}<br>
            Lat: ${hotel.y.toFixed(5)}
        `);
        marker.addTo(hotelsLayer);
        hotelMarkers.push(marker);
    });
    console.log(`Drew ${hotels.length} hotels`);
}

// DRAW POIs - called once
function drawPOIs(pois) {
    // Clear existing POI markers
    poisLayer.clearLayers();
    poiMarkers = [];

    // Draw POIs
    pois.forEach(poi => {
        // Select icon based on tier
        let icon;
        let emoji;
        if (poi.tier === 'A') {
            icon = poiIconTierA;
            emoji = '🏛️';
        } else if (poi.tier === 'B') {
            icon = poiIconTierB;
            emoji = '☕';
        } else {
            icon = poiIconTierC;
            emoji = '🛍️';
        }

        let marker = L.marker([poi.y, poi.x], {icon: icon});
        marker.bindPopup(`
            <b>${emoji} ${poi.name}</b><br>
            Tier: ${poi.tier}<br>
            Type: ${poi.type}<br>
            Lon: ${poi.x.toFixed(5)}<br>
            Lat: ${poi.y.toFixed(5)}
        `);
        marker.addTo(poisLayer);
        poiMarkers.push(marker);
    });
    console.log(`Drew ${pois.length} POIs (A: ${pois.filter(p => p.tier === 'A').length}, B: ${pois.filter(p => p.tier === 'B').length}, C: ${pois.filter(p => p.tier === 'C').length})`);
}

// DRAW ALL AGENTS RECEIVED FROM FASTAPI
function drawAgents(state) {
    residentsLayer.clearLayers();
    touristsLayer.clearLayers();

    // Update time display
    document.getElementById("timeDisplay").innerText =
    `Hour: ${state.hour}:${state.min.toString().padStart(2, '0')}`;

    state.agents.forEach(agent => {
        let color = agent.type === "resident" ? "blue" : "red";
        let layerGroup = agent.type === "resident" ? residentsLayer : touristsLayer;

        let marker = L.circleMarker([agent.y, agent.x], {
            radius: 6,
            color: color
        });

        // Popup with details
        let extra = "";

        if (agent.type === "resident") {
            extra = `Happiness: ${agent.happiness.toFixed(2)}<br>`;
        } else {
            extra = `Noise: ${agent.noise.toFixed(2)}<br>`;
            extra += `Pollution: ${agent.pollution.toFixed(2)}<br>`;
            extra += `Satisfaction: ${agent.satisfaction.toFixed(2)}<br>`;
            extra += `State: ${agent.state}<br>`;
            if (agent.primary_target) {
                extra += `Primary Target: ${agent.primary_target}<br>`;
            } else {
                extra += `Primary Target: None<br>`;
            }
            if (agent.secondary_target) {
                extra += `Secondary Target: ${agent.secondary_target}<br>`;
            } else {
                extra += `Secondary Target: None<br>`;
            }
        }

        marker.bindPopup(`
            <b>${agent.type.toUpperCase()}</b><br>
            ${extra}
            Lon: ${agent.x.toFixed(5)}<br>
            Lat: ${agent.y.toFixed(5)}
        `);

        marker.addTo(layerGroup);
    });
}

// DRAW HEATMAP
function drawHeatmap(heatmapData) {
    // Clear existing heatmap
    heatmapLayer.clearLayers();
    
    const grid = heatmapData.grid;
    const cells = heatmapData.cells;
    const attribute = heatmapData.attribute;
    
    // Find max value for normalization
    let maxValue = 0;
    for (let row = 0; row < grid.length; row++) {
        for (let col = 0; col < grid[row].length; col++) {
            if (grid[row][col] > maxValue) {
                maxValue = grid[row][col];
            }
        }
    }
    
    console.log(`Drawing ${attribute} heatmap, max value: ${maxValue.toFixed(2)}`);
    
    // Draw each cell as a rectangle
    for (let row = 0; row < grid.length; row++) {
        for (let col = 0; col < grid[row].length; col++) {
            const value = grid[row][col];
            
            // Skip cells with no value
            if (value === 0) continue;
            
            // Normalize value (0-1)
            const normalized = maxValue > 0 ? value / maxValue : 0;
            
            // Get cell bounds
            const cell = cells[row][col];
            const bounds = [
                [cell.lat_min, cell.lon_min],
                [cell.lat_max, cell.lon_max]
            ];
            
            // Color based on attribute type
            let color;
            if (attribute === 'noise') {
                // Yellow to Red for noise
                color = `rgb(${Math.floor(255)}, ${Math.floor(255 * (1 - normalized))}, 0)`;
            } else {
                // Green to Brown for pollution
                color = `rgb(${Math.floor(139 * normalized)}, ${Math.floor(69 * (1 - normalized) + 69)}, ${Math.floor(19)})`;
            }
            
            // Create rectangle
            const rectangle = L.rectangle(bounds, {
                color: color,
                fillColor: color,
                fillOpacity: 0.4 + (normalized * 0.4), // 0.4 to 0.8 opacity
                weight: 0
            });
            
            // Add popup with value
            rectangle.bindPopup(`
                <b>${attribute.toUpperCase()}</b><br>
                Value: ${value.toFixed(2)}<br>
                Normalized: ${(normalized * 100).toFixed(1)}%
            `);
            
            rectangle.addTo(heatmapLayer);
        }
    }
    
    console.log(`Heatmap drawn successfully`);
}

// LOAD INITIAL STATE
function loadInitial() {
    fetch("/state")
        .then(res => res.json())
        .then(state => {
            drawAgents(state);
            if (state.hotels) {
                drawHotels(state.hotels);
            }
            if (state.pois) {
                drawPOIs(state.pois);
            }
        });
}
loadInitial();

// STEP ONCE BUTTON
document.getElementById("stepButton").addEventListener("click", () => {
    fetch("/step", { method: "POST" })
        .then(res => res.json())
        .then(state => {
            drawAgents(state);
        });
});

// AUTO RUN
let running = false;
let intervalId = null;

document.getElementById("autoButton").addEventListener("click", () => {
    if (!running) {
        running = true;
        intervalId = setInterval(() => {
            fetch("/step", { method: "POST" })
                .then(res => res.json())
                .then(state => {
                    drawAgents(state);
                });
        }, 500);
    }
});

// STOP AUTO RUN
document.getElementById("stopButton").addEventListener("click", () => {
    running = false;
    clearInterval(intervalId);
});

// SKIP TO MORNING (8:00 AM)
document.getElementById("skipToMorningButton").addEventListener("click", () => {
    console.log("Skipping to 8:00 AM...");
    fetch("/skip_to_morning", { method: "POST" })
        .then(res => res.json())
        .then(state => {
            console.log("Skipped to morning");
            drawAgents(state);
        })
        .catch(err => {
            console.error("Error skipping to morning:", err);
        });
});

// GENERATE NOISE HEATMAP
document.getElementById("noiseHeatmapButton").addEventListener("click", () => {
    console.log("Requesting noise heatmap...");
    fetch("/heatmap/noise")
        .then(res => res.json())
        .then(data => {
            console.log("Noise heatmap data received");
            drawHeatmap(data);
        })
        .catch(err => {
            console.error("Error loading noise heatmap:", err);
        });
});

// GENERATE POLLUTION HEATMAP
document.getElementById("pollutionHeatmapButton").addEventListener("click", () => {
    console.log("Requesting pollution heatmap...");
    fetch("/heatmap/pollution")
        .then(res => res.json())
        .then(data => {
            console.log("Pollution heatmap data received");
            drawHeatmap(data);
        })
        .catch(err => {
            console.error("Error loading pollution heatmap:", err);
        });
});

// CLEAR HEATMAP
document.getElementById("clearHeatmapButton").addEventListener("click", () => {
    heatmapLayer.clearLayers();
    console.log("Heatmap cleared");
});

// CHART INSTANCES
let happinessChart = null;
let satisfactionChart = null;

// GENERATE HAPPINESS GRAPH
document.getElementById("happinessGraphButton").addEventListener("click", () => {
    console.log("Requesting happiness distribution...");
    fetch("/distribution/happiness")
        .then(res => res.json())
        .then(data => {
            console.log("Happiness distribution data received");
            
            // Show chart container
            document.getElementById("happinessChartContainer").classList.add("active");
            
            // Scroll to the chart
            document.getElementById("happinessChartContainer").scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            
            // Destroy existing chart if it exists
            if (happinessChart) {
                happinessChart.destroy();
            }
            
            // Create new chart
            const ctx = document.getElementById('happinessChart').getContext('2d');
            happinessChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: data.labels,
                    datasets: [{
                        label: 'Number of Residents',
                        data: data.counts,
                        backgroundColor: 'rgba(33, 150, 243, 0.6)',
                        borderColor: 'rgba(33, 150, 243, 1)',
                        borderWidth: 2
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: {
                                precision: 0
                            },
                            title: {
                                display: true,
                                text: 'Number of Residents',
                                font: {
                                    size: 14
                                }
                            }
                        },
                        x: {
                            title: {
                                display: true,
                                text: 'Happiness Level Range',
                                font: {
                                    size: 14
                                }
                            }
                        }
                    },
                    plugins: {
                        title: {
                            display: true,
                            text: `Total Residents: ${data.total_residents}`,
                            font: {
                                size: 16
                            }
                        },
                        legend: {
                            display: false
                        }
                    }
                }
            });
        })
        .catch(err => {
            console.error("Error loading happiness distribution:", err);
        });
});

// GENERATE SATISFACTION GRAPH
document.getElementById("satisfactionGraphButton").addEventListener("click", () => {
    console.log("Requesting satisfaction distribution...");
    fetch("/distribution/satisfaction")
        .then(res => res.json())
        .then(data => {
            console.log("Satisfaction distribution data received");
            
            // Show chart container
            document.getElementById("satisfactionChartContainer").classList.add("active");
            
            // Scroll to the chart
            document.getElementById("satisfactionChartContainer").scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            
            // Destroy existing chart if it exists
            if (satisfactionChart) {
                satisfactionChart.destroy();
            }
            
            // Create new chart
            const ctx = document.getElementById('satisfactionChart').getContext('2d');
            satisfactionChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: data.labels,
                    datasets: [{
                        label: 'Number of Tourists',
                        data: data.counts,
                        backgroundColor: 'rgba(156, 39, 176, 0.6)',
                        borderColor: 'rgba(156, 39, 176, 1)',
                        borderWidth: 2
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: {
                                precision: 0
                            },
                            title: {
                                display: true,
                                text: 'Number of Tourists',
                                font: {
                                    size: 14
                                }
                            }
                        },
                        x: {
                            title: {
                                display: true,
                                text: 'Satisfaction Level Range',
                                font: {
                                    size: 14
                                }
                            }
                        }
                    },
                    plugins: {
                        title: {
                            display: true,
                            text: `Total Tourists: ${data.total_tourists}`,
                            font: {
                                size: 16
                            }
                        },
                        legend: {
                            display: false
                        }
                    }
                }
            });
        })
        .catch(err => {
            console.error("Error loading satisfaction distribution:", err);
        });
});

// CLEAR GRAPHS
document.getElementById("clearGraphButton").addEventListener("click", () => {
    // Hide chart containers
    document.getElementById("happinessChartContainer").classList.remove("active");
    document.getElementById("satisfactionChartContainer").classList.remove("active");
    
    // Destroy charts
    if (happinessChart) {
        happinessChart.destroy();
        happinessChart = null;
    }
    if (satisfactionChart) {
        satisfactionChart.destroy();
        satisfactionChart = null;
    }
    
    console.log("Graphs cleared");
});