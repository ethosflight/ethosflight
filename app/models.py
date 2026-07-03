from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class FlightEvent(BaseModel):
    flight_id: str
    aircraft_type: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[int] = None
    speed: Optional[int] = None
    event_type: str
    event_data: Optional[str] = None

class Alert(BaseModel):
    event_id: int
    alert_type: str
    severity: str
    message: str
    engine: str

class WeatherObservation(BaseModel):
    station_id: str
    temperature: Optional[float] = None
    wind_speed: Optional[int] = None
    wind_direction: Optional[int] = None
    visibility: Optional[float] = None
    conditions: Optional[str] = None
    observed_at: datetime
