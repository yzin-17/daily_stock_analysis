-- 原生 FX 表结构；只捕获声明，不访问数据库。
-- src/storage.py SHA-256: f15ff413ad470d712685bfe9bd387837ec1b67130ca772c9a4891bdb5d969749
CREATE TABLE portfolio_fx_rates (
	id INTEGER NOT NULL, 
	from_currency VARCHAR(8) NOT NULL, 
	to_currency VARCHAR(8) NOT NULL, 
	rate_date DATE NOT NULL, 
	rate FLOAT NOT NULL, 
	source VARCHAR(32) NOT NULL, 
	is_stale BOOLEAN NOT NULL, 
	updated_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT uix_portfolio_fx_pair_date UNIQUE (from_currency, to_currency, rate_date)
);
CREATE INDEX ix_portfolio_fx_rates_from_currency ON portfolio_fx_rates (from_currency);
CREATE INDEX ix_portfolio_fx_rates_rate_date ON portfolio_fx_rates (rate_date);
CREATE INDEX ix_portfolio_fx_rates_to_currency ON portfolio_fx_rates (to_currency);

