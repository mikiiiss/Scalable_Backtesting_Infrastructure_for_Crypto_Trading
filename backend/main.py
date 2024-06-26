from typing import List
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError
import pandas as pd
import threading

from . import models, schemas, database
from .auth import router as auth_router
from kafka_topic.kafka_config import get_kafka_producer, SCENE_TOPIC, RESULT_TOPIC, get_kafka_consumer

from backend.utils.backtest import run_backtest
from backend.utils.init_data import initialize_data

get_db = database.get_db

consumer = get_kafka_consumer(SCENE_TOPIC)
producer = get_kafka_producer()

models.Base.metadata.create_all(bind=database.engine)

app = FastAPI()

@app.on_event("startup") # TODO update the code with lifespan dependency
def start_kafka_consumer():
    threading.Thread(target=consume_scene_parameters).start()

@app.on_event("startup") # TODO update the code with lifespan dependency
def on_startup():
    db = next(get_db())
    initialize_data(db)

app.include_router(auth_router, prefix="/auth", tags=["auth"])

@app.get('/health')
def check_health():
    return "API is working and healthy"

@app.post('/indicators/', response_model=schemas.Indicator)
def create_indicator(indicator: schemas.IndicatorCreate, db: Session = Depends(get_db)):
    db_indicator = models.Indicator(**indicator.model_dump())
    db.add(db_indicator)
    db.commit()
    db.refresh(db_indicator)
    return db_indicator

@app.get('/indicators/', response_model=List[schemas.Indicator])
def read_indicators(skip: int = 0, limit: int = 10, db: Session = Depends(get_db)):
    indicators = db.query(models.Indicator).offset(skip).limit(limit).all()
    return indicators

@app.post('/stocks/', response_model=schemas.Stock)
def create_stock(stock: schemas.StockCreate, db: Session = Depends(get_db)):
    db_stock = models.Stock(**stock.model_dump())
    db.add(db_stock)
    db.commit()
    db.refresh(db_stock)
    return db_stock

@app.get('/stocks/', response_model=List[schemas.Stock])
def read_stocks(skip: int = 0, limit: int = 20, db: Session = Depends(get_db)):
    stocks = db.query(models.Stock).offset(skip).limit(limit).all()
    return stocks

@app.post('/scenes/', response_model=schemas.Scene)
def create_scene(scene: schemas.SceneCreate, db: Session = Depends(get_db)):
    try:
        db_scene = models.Scene(**scene.dict())
        db.add(db_scene)
        db.commit()
        db.refresh(db_scene)
        print("Scene created successfully:", db_scene)

        # Send scene parameters to Kafka
        scene_parameters = {
            'period': db_scene.period,
            'indicator_name': db_scene.indicator.name,
            'indicator_symbol': db_scene.indicator.symbol,
            'stock_name': db_scene.stock.name,
            'stock_symbol': db_scene.stock.symbol,
            'start_date': db_scene.start_date.strftime('%Y-%m-%d'),
            'end_date': db_scene.end_date.strftime('%Y-%m-%d')
        }
        print("Sending scene parameters to Kafka:", scene_parameters)
        producer.send(SCENE_TOPIC, scene_parameters)
        producer.flush()

        return db_scene
    except IntegrityError:
        db.rollback()
        existing_scene = db.query(models.Scene).filter(
            models.Scene.start_date == scene.start_date,
            models.Scene.end_date == scene.end_date,
            models.Scene.indicator_id == scene.indicator_id
        ).first()
        return existing_scene

@app.delete('/scenes/{scene_id}', response_model=schemas.Scene)
def delete_scene(scene_id: int, db: Session = Depends(get_db)):
    db_scene = db.query(models.Scene).options(joinedload(models.Scene.indicator), joinedload(models.Scene.stock)).filter(models.Scene.id == scene_id).first()
    if db_scene is None:
        raise HTTPException(status_code=404, detail="Scene not found")
    db.delete(db_scene)
    db.commit()
    return db_scene

@app.get('/scenes/{scene_id}', response_model=schemas.Scene)
def read_scene(scene_id: int, db: Session = Depends(get_db)):
    db_scene = db.query(models.Scene).options(joinedload(models.Scene.indicator), joinedload(models.Scene.stock)).filter(models.Scene.id == scene_id).first()
    if db_scene is None:
        raise HTTPException(status_code=404, detail="Scene not found")
    return db_scene

@app.get('/scenes/', response_model=List[schemas.Scene])
def read_scenes(skip: int = 0, limit: int = 10, db: Session = Depends(get_db)):
    scenes = db.query(models.Scene).offset(skip).limit(limit).all()
    return scenes

@app.post('/backtests/{scene_id}', response_model=List[schemas.BacktestResult])
def perform_backtest(scene_id: int, db: Session = Depends(get_db)):
    db_scene = db.query(models.Scene).filter(models.Scene.id == scene_id).first()
    if db_scene is None:
        raise HTTPException(status_code=404, detail="Scene not found")

    # Fetch data based on the scene's date range
    df = fetch_data(db_scene.start_date, db_scene.end_date)
    
    # Perform backtest
    metrics = run_backtest({
        'period': db_scene.period,
        'indicator_name': db_scene.indicator.name
    }, df)

    # Save metrics to database
    backtest_results = []
    for metric in metrics:
        db_backtest_result = models.BacktestResult(scene_id=scene_id, **metric)
        db.add(db_backtest_result)
        db.commit()
        db.refresh(db_backtest_result)
        backtest_results.append(db_backtest_result)

    return backtest_results

@app.get('/run_backtest/', response_model=List[schemas.BacktestResult])
def run_backtests(scene: schemas.SceneCreate, db: Session = Depends(get_db)):
    db_scene = models.Scene(**scene.model_dump())
    return db_scene

@app.get('/backtest_results/', response_model=List[schemas.BacktestResult])
def read_backtest_results(skip: int = 0, limit: int = 10, db: Session = Depends(get_db)):
    backtest_results = db.query(models.BacktestResult).offset(skip).limit(limit).all()
    return backtest_results

def fetch_data(start_date, end_date):
    # Replace this with actual data fetching logic
    df = pd.read_csv('data/binance_btc_usdt_candlestick.csv', index_col='timestamp', parse_dates=True)
    return df.loc[start_date:end_date]

def fetch_existing_backtest(scene_parameters):
    # Function to fetch existing backtest results from the database
    db = next(get_db())
    existing_scene = db.query(models.Scene).filter(
        models.Scene.start_date == scene_parameters['start_date'],
        models.Scene.end_date == scene_parameters['end_date'],
        models.Scene.period == scene_parameters['period'],
        # models.Scene.stock.symbol == scene_parameters['stock_symbol'],
        # models.Scene.indicator.symbol == scene_parameters['indicator_symbol']
    ).first()
    if existing_scene:
        existing_backtests = db.query(models.BacktestResult).filter(
            models.BacktestResult.scene_id == existing_scene.id
        ).all()
        return existing_backtests
    return None

def consume_scene_parameters():
    consumer = get_kafka_consumer(SCENE_TOPIC)
    for message in consumer:
        scene_parameters = message.value
        print(f"Received scene parameters: {scene_parameters}")
        existing_results = fetch_existing_backtest(scene_parameters)
        if existing_results:
            print(f"Using existing backtest results for parameters: {scene_parameters}")
            continue
        # Run the backtest if no existing results
        df = fetch_data(scene_parameters['start_date'], scene_parameters['end_date'])
        metrics = run_backtest(scene_parameters, df)
        producer = get_kafka_producer()
        print(f"Sending backtest results: {metrics}")
        producer.send(RESULT_TOPIC, metrics)
        producer.flush()