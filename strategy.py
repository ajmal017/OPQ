
'''
Implementations of (daily) Pair's Trading strategies.

'''

import time

import pandas as pd



class Strategy:
    '''
    The abstract base class strategy.
    '''

    def __init__(self):
        self._positions = {}
        self._stock_data = {}
        self.today = time.strftime("%Y-%m-%d")


    def now(self, date=None):
        '''
        Get/Set the point of time of the strategy.
        {date} should be a string
        '''
        
        if date is not None:
            self.today = date
        return self.today
    

    def feed(self, stock_data):
        '''
        Feed the strategy with latest stock data.
        '''

        for stock, stock_df in stock_data.items():
            if stock in self._stock_data:
                df = pd.concat([self._stock_data[stock], stock_df])
                df = df[~df.index.duplicated(keep='last')]
                self._stock_data[stock] = df
            else:
                self._stock_data[stock] = stock_df


    def positions(self, param_positions=None, incremental=False):
        '''
        Get or set the stock positions.
        The object representing the positions is of type dict. The key is the stock code
        and the value is the number of shares in held.
        A positive value indicates a LONG position while a negative value indicates a SHORT position.
        '''
        
        if not param_positions is None:
            if incremental:
                for stock, position in param_positions.items():
                    if not stock in self._positions:
                        self._positions[stock] = 0
                    self._positions[stock] += position
            else:
                self._positions = param_positions.copy()
        return self._positions.copy()
        

    def decide(self):
        '''
        Make buy / sell / short decisions.

        This method should be overrided in subclass.
        '''

        return {}



class HoldingPair:
    '''
    A pair of stock (X, Y) in held.
    The resulted asset is (Y - beta * X)
    '''

    def __init__(self, X, Y, beta=1):
        self.X = X
        self.Y = Y
        self.beta = beta
        self.spread_mean = 0
        self.spread_std = 0
        self.money_allocated = 0
        self.position = 0
        self.X_quantity = 0
        self.Y_quantity = 0


class PairTradeStrategy(Strategy):
    '''
    Pair trading strategy.
    '''

    @staticmethod
    def select_pairs(file, num_pairs, metric=None, ascending=False, unique=False, beta=None, filter=None):
        '''
        Choose pairs of stocks.
        '''

        if type(file) is str:
            df = pd.read_csv(file)
        else:
            df = file
        stock_codes = {} # No repeat stock
        if metric is not None:
            df.sort_values(metric, ascending=ascending, inplace=True)
        pairs = []
        for index, row in df.iterrows():
            if len(pairs) >= num_pairs:
                break
            stock_x = row['Stock_1']
            stock_y = row['Stock_2']
            if unique and stock_x in stock_codes or stock_y in stock_codes:
                continue
            if filter and not filter(row):
                continue
            stock_codes[stock_x] = True
            stock_codes[stock_y] = True
            beta_ = row[beta] if beta else 1
            pairs.append({
                'Stock_1': stock_x,
                'Stock_2': stock_y,
                'beta': beta_
            })
        return pairs


    @staticmethod
    def dump_pairs(filename, pairs):
        '''
        Export the pairs into a csv file.
        '''
        df = pd.DataFrame(columns=["Stock_1", "Stock_2", "beta"])
        for pair in pairs:
            df = df.append(pair, ignore_index=True)
        df.to_csv(filename, index=False)

  
    @staticmethod
    def load_pairs(filename):
        '''
        Load pairs from a csv file.
        '''
        
        pairs = []
        df = pd.read_csv(filename)
        for index, row in df.iterrows():
            pairs.append({
                'Stock_1': row['Stock_1'],
                'Stock_2': row['Stock_2'],
                'beta': row['beta']
            })
        return pairs
    

    def __init__(self, pairs=[], thresholds=[1,2,3], allocations=[1]):
        '''
        {thresholds} should be a list of size n.
        The first is the exit threshold and the last is the stop loss threshold.
        
        {allocations} should be a list of size n - 2.
        '''
        
        super().__init__()
        thresholds = sorted([abs(t) for t in thresholds])
        self.threshold_exit = thresholds[0]
        self.threshold_stop = thresholds[-1]
        self.thresholds_enter = thresholds[1:-1]
        self.allocations = [abs(t) for t in allocations]
        assert len(self.thresholds_enter) == len(self.allocations)
        assert sum(self.allocations) <= 1
        
        if type(pairs) is str:
            pairs = PairTradeStrategy.load_pairs(pairs)
        self.pairs = []
        for pair in pairs:
            self.pairs.append(HoldingPair(pair['Stock_1'], pair['Stock_2'], pair['beta']))
        self.tx_history = []


    def load_pair_info(self, filename):
        '''
        Load the pair information from a local csv file.
        '''

        self.pairs = []
        df = pd.read_csv(filename)
        for i, row in df.iterrows():
            pair = HoldingPair("", "")
            for k, v in row.items():
                setattr(pair, k, v)
            self.pairs.append(pair)

    def dump_pair_info(self, filename):
        '''
        Export the pair information to a local csv file.
        '''
        
        df = pd.DataFrame(columns=["X", "Y", "beta", "spread_mean", "spread_std", "money_allocated", "position", "X_quantity", "Y_quantity"])
        for pair in self.pairs:
            df = df.append(pair.__dict__, ignore_index=True)
        df.to_csv(filename, index=False)
        

    def allocate_money(self, cash):
        '''
        Notify the strategy the total amount of (leveraged) cash allocated for it.
        '''
        
        money_each_pair = cash / len(self.pairs)
        for pair in self.pairs:
            pair.money_allocated = money_each_pair


    def transaction_history(self):
        '''
        Get the transaction history in pandas dataframe format.
        '''
        
        history = pd.DataFrame(columns=["Date", "Stock", "Direction", "Quantity", "Price"])
        for date, orders in self.tx_history:
            for stock, quantity in orders.items():
                if quantity != 0:
                    order = {"Date": date, "Stock": stock}
                    order["Quantity"] = abs(quantity)
                    order["Direction"] = "Buy" if quantity > 0 else "Sell"
                    order["Price"] = self._stock_data[stock].loc[date]['CLOSE']
                    history = history.append(order, ignore_index=True)
        history.sort_values("Date", inplace=True)
        return history
    

    def analyze_spread(self, start, end):
        '''
        For each pair, update the mean and stdev of its spread.
        '''

        for pair in self.pairs:
            df_stock_x = self._stock_data[pair.X].loc[start:end]
            df_stock_y = self._stock_data[pair.Y].loc[start:end]
            df = pd.DataFrame(columns=['spread'])
            df['spread'] = df_stock_y['CLOSE'] - pair.beta * df_stock_x['CLOSE']
            pair.spread_mean = df['spread'].mean()
            pair.spread_std = df['spread'].std()
            

    def detect_level(self, pair, x_price=None, y_price=None):
        '''
        For a given pair of stocks, calculate the current signal level of their spread.
        The return value is an integer. Suppose there are n enter thresholds:
        
            n+2    stop loss threshold and above
            n+1    n-th enter threshold to stop loss threshold
            ...
            1      exit threshold to 1st enter threshold
            0      negative exit threshold to exit threshold
            -1     negative exit threshold to negative 1st enter threshold
            ...
            -(n+1) negative n-th enter threshold to negative stop loss threshold
            -(n+2) negative stop loss threshold and below
        '''

        if x_price is None:
            df_stock_x = self._stock_data[pair.X].loc[:self.today]
            x_price = df_stock_x.iloc[-1]['CLOSE']
        if y_price is None:
            df_stock_y = self._stock_data[pair.Y].loc[:self.today]
            y_price = df_stock_y.iloc[-1]['CLOSE']

        cur_spread = y_price - pair.beta * x_price
        cur_spread_z = (cur_spread - pair.spread_mean) / pair.spread_std
        thresholds_all = [self.threshold_exit] + self.thresholds_enter + [self.threshold_stop]
        level = 0
        for t in thresholds_all:
            if abs(cur_spread_z) >= t:
                level += 1
        if cur_spread_z < 0:
            level = -level
        return level


    def derive_target_positions(self, stock_prices=None):
        '''
        Derive the target positions by stock.
        For each pair, calculate the pair's target position.
        Then finally sum up the pairs' stock positions to aggregated stock positions.
        '''

        target_positions = {}
        for pair in self.pairs:
            if stock_prices is not None:
                x_price = stock_prices[pair.X]
                y_price = stock_prices[pair.Y]
            else:
                x_price = self._stock_data[pair.X].loc[self.today]['CLOSE']
                y_price = self._stock_data[pair.Y].loc[self.today]['CLOSE']
            pair_price = y_price + abs(pair.beta) * x_price

            level = self.detect_level(pair, x_price, y_price)
            if level == 0:
                # Between positive exit and negative exit, target position is empty
                target_pair_position = 0
            elif abs(level) == 1:
                # Between exit and 1st enter, target position is the same as current position
                target_pair_position = pair.position
            elif abs(level) == len(self.thresholds_enter) + 2:
                # Beyond stop loss threshold, target position is empty
                target_pair_position = 0
            else:
                # Target position is Long if level < 0, Short if level > 0 
                direction = 1 if level < 0 else -1
                target_pair_position = direction * (abs(level) - 1)

            # Derive the target quantity of X and Y
            money_alloc = pair.money_allocated * sum(self.allocations[:abs(target_pair_position)])
            if target_pair_position > 0:
                # Target position is LONG
                Y_target_quantity = int(money_alloc / pair_price)
            elif target_pair_position < 0:
                # Target position is SHORT
                Y_target_quantity = -int(money_alloc / pair_price)
            else:
                # Target position is EMPTY
                Y_target_quantity = 0
            X_target_quantity = -int(Y_target_quantity * pair.beta)
            pair.position = target_pair_position
            pair.X_quantity = X_target_quantity
            pair.Y_quantity = Y_target_quantity

            # Update the target by-stock positions
            target_positions[pair.X] = target_positions.get(pair.X, 0) + X_target_quantity
            target_positions[pair.Y] = target_positions.get(pair.Y, 0) + Y_target_quantity

        return target_positions     
        

    def decide(self, stock_prices=None):
        '''
        First, get the target by-stock positions.
        For each stock, make trades if its target position is different from its current position.
        '''

        target_positions = self.derive_target_positions(stock_prices)
        current_positions = self.positions()
        orders = {}
        for stock_code, current_position in current_positions.items():
            if stock_code not in target_positions:
                orders[stock_code] = -current_position
        for stock_code, target_position in target_positions.items():
            current_position = current_positions.get(stock_code, 0)
            if target_position != current_position:
                orders[stock_code] = target_position - current_position

        self.tx_history.append([self.today, orders])
        return orders




