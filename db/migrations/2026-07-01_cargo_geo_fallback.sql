-- Автоматическая примерная точка загрузки для грузов без гео.
-- Точную геолокацию пользователя не перезаписывает.

CREATE OR REPLACE FUNCTION set_cargo_geo_fallback()
RETURNS trigger AS $$
DECLARE
  city text;
BEGIN
  city := lower(coalesce(NEW.from_city, ''));

  -- Если точка уже есть, ничего не трогаем
  IF NEW.load_latitude IS NOT NULL AND NEW.load_longitude IS NOT NULL THEN
    RETURN NEW;
  END IF;

  -- Смоленская область / частые города
  IF city LIKE '%смоленск%' THEN
    NEW.load_latitude := 54.7826;
    NEW.load_longitude := 32.0453;

  ELSIF city LIKE '%ярцев%' THEN
    NEW.load_latitude := 55.0667;
    NEW.load_longitude := 32.7000;

  ELSIF city LIKE '%сафонов%' THEN
    NEW.load_latitude := 55.1167;
    NEW.load_longitude := 33.2333;

  ELSIF city LIKE '%кардымов%' THEN
    NEW.load_latitude := 54.8900;
    NEW.load_longitude := 32.4300;

  ELSIF city LIKE '%велиж%' THEN
    NEW.load_latitude := 55.6000;
    NEW.load_longitude := 31.2000;

  ELSIF city LIKE '%вязьм%' THEN
    NEW.load_latitude := 55.2100;
    NEW.load_longitude := 34.3000;

  ELSIF city LIKE '%рославл%' THEN
    NEW.load_latitude := 53.9500;
    NEW.load_longitude := 32.8600;

  -- Частые направления
  ELSIF city LIKE '%москва%' THEN
    NEW.load_latitude := 55.7558;
    NEW.load_longitude := 37.6176;

  ELSIF city LIKE '%саратов%' THEN
    NEW.load_latitude := 51.5336;
    NEW.load_longitude := 46.0343;

  ELSIF city LIKE '%брянск%' THEN
    NEW.load_latitude := 53.2521;
    NEW.load_longitude := 34.3717;

  ELSIF city LIKE '%калуга%' THEN
    NEW.load_latitude := 54.5293;
    NEW.load_longitude := 36.2754;

  ELSIF city LIKE '%твер%' THEN
    NEW.load_latitude := 56.8587;
    NEW.load_longitude := 35.9176;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_cargo_geo_fallback ON cargo;

CREATE TRIGGER trg_cargo_geo_fallback
BEFORE INSERT OR UPDATE OF from_city, load_latitude, load_longitude
ON cargo
FOR EACH ROW
EXECUTE FUNCTION set_cargo_geo_fallback();

-- Применить к уже существующим открытым грузам без координат
UPDATE cargo
SET from_city = from_city
WHERE status = 'open'
  AND (load_latitude IS NULL OR load_longitude IS NULL);
