import os, hashlib, requests, zipfile, tarfile, torch, random, time, math, collections
import matplotlib.pyplot as plt
import text_pretreatment
from torch import nn
from torch.nn import functional as F
from torch.utils import data

DATA_URL = 'http://d2l-data.s3-accelerate.amazonaws.com/'

class Accumulator:  # 累加多个变量的实用程序类
    def __init__(self, n):
        self.data = [0.0]*n

    def add(self, *args):  # 在data的对应位置加上对应的数
        self.data = [a + float(b) for a, b in zip(self.data, args)]

    def reset(self):
        self.data = [0.0] * len(self.data)

    def __getitem__(self, idx):
        return self.data[idx]

class ResVisualization:
    def __init__(self, xlist: tuple | list, ylist: tuple | list, legend_names: tuple | list, is_grid=None,
                 xlabel: str = None, ylabel: str = None, title: str = None,
                 xlim: list = None, ylim: list = None, line_style: str = '-') -> None:
        """
        xlist : 二维数组,每一行代表一个曲线的x坐标\n
        ylist : 二维数组,每一行代表一个曲线的y坐标\n
        legend_names : 列表，代表每条曲线的名字\n
        is_grid : 是否显示网格\n
        xlabel : x轴的名字\n
        ylabel : y轴的名字\n
        title : 图的名字\n
        xlim : x轴的范围\n
        ylim : y轴的范围\n
        line_style : 曲线的样式\n
        """
        self.res_dict = {name: (x, y) for name, x, y 
                        in zip(legend_names, xlist, ylist)}
        self.is_grid = is_grid
        self.xlim = xlim
        self.ylim = ylim
        self.xlabel = xlabel
        self.ylabel = ylabel
        self.title = title
        self.line_style = line_style

    def add(self, x_val, y_val, name):
        """向名为name的曲线中添加一个(x_val, y_val)数据对"""
        self.res_dict[name][0].append(x_val)
        self.res_dict[name][1].append(y_val)

    def plot_res(self):
        for name, xy_pair in self.res_dict.items():
            plt.plot(xy_pair[0], xy_pair[1], label=name,
                     linestyle=self.line_style)
        if self.is_grid:
            plt.grid()
        if self.title is not None:
            plt.title(self.title)
        if self.xlabel is not None:
            plt.xlabel(self.xlabel)
        if self.ylabel is not None:
            plt.ylabel(self.ylabel)
        if self.xlim is not None:
            plt.xlim(self.xlim)
        if self.ylim is not None:
            plt.ylim(self.ylim)
        plt.legend()
        plt.show()

class Timer:
    """一个计时器类,含有start, stop, get_elapesd_time方法"""
    def __init__(self):
        self.start_time = None
        self.end_time = None
        self.elapsed_time = None
        self.elapsed_time_sum = 0

    def start(self):
        self.start_time = time.time()
        self.end_time = None
        self.elapsed_time = None

    def stop(self):
        if self.start_time is None:
            raise ValueError("计时器还没有开始计时")
        self.end_time = time.time()
        self.elapsed_time = self.end_time - self.start_time
        self.elapsed_time_sum += self.elapsed_time

    def get_elapsed_time(self):
        if self.elapsed_time is None:
            raise ValueError("计时器未被停止计时")
        return self.elapsed_time

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()
        # print(f"Elapsed time: {self.get_elapsed_time():.4f} seconds")

class RNNScratch():
    def __init__(self, vocab_size, num_hiddens, init_params_fn, forward_fn, init_state_fn, device) -> None:
        self.vocab_size, self.num_hiddens = vocab_size, num_hiddens
        self.params = init_params_fn(vocab_size, num_hiddens, device)
        self.forward_fn, self.init_state_fn = forward_fn, init_state_fn

    def begin_state(self, batch_size, device):
        return self.init_state_fn(batch_size, self.num_hiddens, device)

    def __call__(self, X, state):
        """
        参数:\n
        X : 形状为(批量大小, 时间步数量)\n
        state : 前一个时间步的隐状态，张量变量，形状为(批量大小,隐藏单元数)
        """
        X = F.one_hot(X.T, self.vocab_size).type(torch.float32)
        return self.forward_fn(X, state, self.params)

class RNN(nn.Module):
    def __init__(self, rnn_layer, vocab_size):
        super().__init__()
        self.rnn_layer = rnn_layer # rnn_layer的"输出"(H)不涉及输出层的计算：它是指每个时间步的隐状态，这些隐状态可以用作后续输出层的输入
        self.vocab_size = vocab_size
        self.num_hiddens = self.rnn_layer.hidden_size
        if not self.rnn_layer.bidirectional: # 如果RNN不是双向的,num_directions应该是1
            self.num_directions = 1
            self.linear = nn.Linear(self.num_hiddens, self.vocab_size)
        else: # 如果RNN是双向的,num_directions应该是2
            self.num_directions = 2
            self.linear = nn.Linear(self.num_hiddens * 2, self.vocab_size)

    def forward(self, inputs, state):
        """
        参数:\n
        inputs: 批量矩阵X转置后的独热编码,形状为(时间步数量, 批量大小, 词表大小), 每一行是批量矩阵中各个样本在某一时间步的特征\n
        state: 上一时间步的隐状态，张量变量，形状为(批量大小,隐藏单元数)
        返回:\n
        形状为(num_steps*batch_size, num_outputs)的预测序列 和 隐状态
        """
        X = F.one_hot(inputs.T.long(), self.vocab_size).to(torch.float32) # 将输入转成one-hot向量表示 shape=(num_steps, batch_size, vocab_size)
        H, state = self.rnn_layer(X, state) # H.shape=(num_steps, batch_size, num_hiddens)
        output = self.linear(H.reshape((-1, H.shape[-1]))) # 全连接层首先将H的形状改为(时间步数*批量大小,隐藏单元数),它的输出形状是(时间步数*批量大小,词表大小)。
        return output, state
    
    def begin_state(self, device, batch_size=1):
        """
        返回隐藏层的初始状态\n
        参数:\n
        batch_size : 批量大小\n
        返回:\n
        若隐藏层是nn.GRU,返回形状为(num_layers * num_directions, batch_size, hidden_size)的全0张量隐状态\n
        若隐藏层是nn.LSTM,返回形状为(2, num_layers * num_directions, batch_size, hidden_size)的全0张量隐状态
        """
        if not isinstance(self.rnn_layer, nn.LSTM): # nn.GRU以张量作为隐状态
            return torch.zeros(size=(self.num_directions * self.rnn_layer.num_layers, batch_size, self.num_hiddens),
                                device=device)
        else: # nn.LSTM以元组作为隐状态
            return (torch.zeros(size=(self.num_directions * self.rnn_layer.num_layers, batch_size, self.num_hiddens),
                                device=device),
                    torch.zeros(size=(self.num_directions * self.rnn_layer.num_layers, batch_size, self.num_hiddens),
                                device=device))
        
class Vocabulary:
    def __init__(self, tokens=None, min_freq=0, reserved_tokens=None) -> None:
        """"
        tokens : 词元列表
        min_freq : 最小的词元出现次数
        reserved_tokens : 保留的词元列表
        """
        if tokens is None:
            tokens = []
        if reserved_tokens is None:
            reserved_tokens = []
        counter = self.count_corpus(tokens)
        self._token_freqs = sorted(counter.items(), key=lambda x:x[1], reverse=True) # 按出现频率降序排序
        self.idx_to_token = ['<unk>'] + reserved_tokens # 未知的词元索引为0
        self.token_to_idx = {token:idx for idx, token in enumerate(self.idx_to_token)}  # 单词到索引的映射
        for token, freq in self._token_freqs: # 根据min_freq规则,过滤部分tokens中的词 剩下的添加到单词表
            if freq < min_freq:
                break
            if token not in self.token_to_idx: # 如果当前单词不在保留单词中,则添加到单词表中
                self.idx_to_token.append(token)
                self.token_to_idx[token] = len(self.idx_to_token) - 1

    def count_corpus(self, tokens):
        """
        统计词元的出现频率
        tokens : 1D或2D列表
        """
        if len(tokens) == 0 or isinstance(tokens[0], list): # 若是空列表或二维列表,则展平为一维列表
            # 将二维列表展平成一个一维列表
            tokens = [token for line in tokens for token in line]
        return collections.Counter(tokens)

    def __len__(self):
        return len(self.idx_to_token)
    
    def __getitem__(self, tokens):
        if not isinstance(tokens, (list,tuple)): # 如果不是列表或元组,则直接以单个键的方式返回一个token的索引
            return self.token_to_idx.get(tokens, self.unk)
        return [self.__getitem__(token) for token in tokens] # 以多个键方式返回若干个token的索引列表
    
    def to_tokens(self, indices):
        if not isinstance(indices, (list, tuple)):
            return self.idx_to_token[indices]
        return [self.idx_to_token[index] for index in indices]
    
    @property
    def num_tokens(self):
        return len(self)
    
    @property
    def unk(self):
        return 0 # 未知的单词索引为0
    
    @property
    def token_freqs(self):
        return self._token_freqs

class Encoder(nn.Module):
    """编码器:接受一个长度可变的序列作为输入，并将其转换为具有固定形状的编码状态。"""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def forward(self, X, *args):
        raise NotImplementedError
    
class Decoder(nn.Module):
    """解码器:它将固定形状的编码状态映射到长度可变的序列。"""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def init_state(self, encoder_outputs, *args):
        """用于将编码器的输出转换为编码后的状态。注意，此步骤可能需要额外的输入"""
        raise NotImplementedError
    
    def forward(self, X, state):
        raise NotImplementedError
    
class EncoderDecoder(nn.Module):
    """编码器-解码器架构的基类"""
    def __init__(self, encoder, decoder, **kwargs) -> None:
        super().__init__(**kwargs)
        self.encoder = encoder
        self.decoder = decoder

    def forward(self, encoder_inputs, decoder_inputs, *args):
        encoder_outputs = self.encoder(encoder_inputs)
        decoder_state = self.decoder.init_state(encoder_outputs, *args)
        return self.decoder(decoder_inputs, decoder_state)

def download(DATA_HUB, name, save_folder_name: str):
    """
    下载一个DATA_HUB中的name文件并返回本地文件名\n
    参数:\n
        save_folder_name指定存储在当前目录下的data/save_folder_name下
    """
    assert name in DATA_HUB, f"{name} 不存在于 {DATA_HUB}"
    cache_dir = os.path.join('data', save_folder_name)
    url, sha1_hash = DATA_HUB[name]
    os.makedirs(cache_dir, exist_ok=True)
    fname = os.path.join(cache_dir, url.split('/')[-1])
    if os.path.exists(fname):
        sha1 = hashlib.sha1()  # 计算给定字符串的SHA-1哈希值
        with open(fname, 'rb') as f:
            while True:
                data = f.read(1048576)  # 参数:读取1MB内容
                if not data:
                    break
                sha1.update(data)
        if sha1.hexdigest() == sha1_hash:  # 检查哈希值判定文件是否已经存在
            return fname  # 命中缓存
    print(f'正在从{url}下载{fname}...')
    r = requests.get(url, stream=True, verify=True)
    with open(fname, 'wb') as f:
        f.write(r.content)
    return fname

def download_extract(DATA_HUB, name, save_folder_name):
    """下载并解压zip/tar文件"""
    compressed_file = download(DATA_HUB, name, save_folder_name) # 下载压缩包
    base_dir = os.path.dirname(compressed_file)  # basedir为压缩包所在相对路径 dirname()获取文件的路径
    data_dir, ext = os.path.splitext(compressed_file) # splitext()将文件名与文件后缀(如.zip)分割为具有两元素的元组
    if ext == '.zip':
        fp = zipfile.ZipFile(compressed_file, 'r')
    elif ext in ('.tar', '.gz'):
        fp = tarfile.open(compressed_file, 'r')
    else:
        raise ValueError('只有zip/tar文件可以被解压缩')
    fp.extractall(base_dir)  # 将压缩的文件解压到base_dir路径下
    return data_dir

def download_all(DATA_HUB):
    """下载DATA_HUB中的所有文件"""
    for name in DATA_HUB:
        download(name)

def load_array(data_arrays, batch_size, is_train=True):
    """
    将data_arrays中的array打包成TensorDataset后加载到DataLoader中\n
    参数:\n
        data_arrays : tuple(tuple)\n 每一个array的第一维长度必须一致
    返回:\n
        一个DataLoader类的data_iter
    """
    dataset = data.TensorDataset(*data_arrays)
    return data.DataLoader(dataset, batch_size, shuffle=is_train)

def try_gpu(i=0):
    """如果存在,返回gpu(i), 否则返回cpu()"""
    if torch.cuda.device_count() >= i+1:
        return torch.device(f'cuda:{i}')
    return torch.device('cpu')

def sgd(params: list, lr, batch_size):
    """
    小批量梯度下降优化函数\n
    参数:\n
    params : 模型的所有可学习的参数的列表\n
    lr : 学习率\n
    batch_size : 批量大小\n
    """
    with torch.no_grad():
        for param in params:
            param -= lr * param.grad / batch_size  # 更新参数
            param.grad.zero_()  # 清除累积的梯度

def grad_clipping(net, theta):
    """
    进行梯度裁剪的函数\n
    参数:\n
    net : 训练过程中需要进行梯度裁剪的神经网络\n
    theta : 一个阈值,如果梯度梯度的L2范数超过了这个阈值,就将梯度缩放到这个阈值\n
    作用:防止梯度爆炸\n

    梯度爆炸:
    在训练过程中，由于网络的深度较深，反向传播时梯度在每个时间步都会累积。如果在某些层的权重初始化得不当，或者激活函数没有选择好，梯度可能会在反向传播过程中逐渐增大。
    梯度变得非常大，导致参数更新时步长过大。这会使得模型参数发生剧烈的变化，甚至导致模型无法收敛，损失函数变得无限大。
    """
    if isinstance(net, nn.Module):
        params = [p for p in net.parameters() if p.requires_grad]
    else:
        params = net.params
    norm = torch.sqrt(sum(torch.sum((p.grad ** 2)) for p in params))
    if norm > theta:
        for param in params:
            param.grad[:] *= theta / norm

class SeqDataLoader:
    """
    加载序列数据的迭代器
    """

    def __init__(self, batch_size, num_steps, max_tokens, use_random_iter) -> None:
        if use_random_iter:
            self.data_iter_fn = self.get_random_batch_seq
        else:
            self.data_iter_fn = self.get_sequential_batch_seq
        self.corpus, self.vocab = text_pretreatment.load_time_machine_corpus(
            max_tokens)
        self.batch_size, self.num_steps = batch_size, num_steps

    def __iter__(self):
        return self.data_iter_fn(self.corpus, self.batch_size, self.num_steps)

    def get_random_batch_seq(self, corpus, batch_size, num_steps):
        """
        使用随机抽样生成一个样本批量\n
        参数:\n
        corpus : 语料库
        batch_size : 一个小批量中有多少个子序列样本
        num_steps : 每个序列预定义的时间步\n
        返回:\n
        X : 特征, shape=(batch_size, num_steps)
        Y : 标签, shape=(batch_size, num_steps)
        """
        def get_seq(pos):
            """
            返回从pos位置开始的长度为num_steps的序列\n
            pos : 一个偏移量
            """
            return corpus[pos: pos + num_steps]

        # 随机选择起始分区的偏移量,随机范围包括num_steps-1  减去1是因为需要考虑标签
        corpus = corpus[random.randint(0, num_steps - 1):]
        num_subseqs = (len(corpus) - 1) // num_steps  # 整个语料库可划分出的子序列的数量
        # 长度为num_step的每个子序列的起始索引
        initial_indices = list(range(0, num_subseqs * num_steps, num_steps))
        # 在随机抽样的迭代过程中,来自两个相邻的、随机的、小批量中的子序列不一定在原始序列上相邻
        random.shuffle(initial_indices)
        # 所有子序列可被分成的小批量的数量 即以batch_size个样本为一批,可分出多少批
        num_batches = num_subseqs // batch_size
        for i in range(0, batch_size * num_batches, batch_size):  # 迭代小批量
            # 得到一批中所有样本的起始索引
            initial_indices_per_batch = initial_indices[i: i+batch_size]
            # 根据起始索引依次获得一批中的样本 X.shape=(batch_size, num_steps)
            X = [get_seq(j) for j in initial_indices_per_batch]
            # 根据起始索引依次获得一批中的样本的标签
            Y = [get_seq(j+1) for j in initial_indices_per_batch]
            yield torch.tensor(X), torch.tensor(Y)  # 特征 和 对应的标签

    def get_sequential_batch_seq(self, corpus, batch_size, num_steps):
        """
        使用顺序分区生成一个样本批量\n
        参数:\n
        corpus : 语料库
        batch_size : 一个小批量中有多少个子序列样本
        num_steps : 每个序列预定义的时间步数\n
        返回:\n
        X : 特征, shape=(batch_size, num_steps)
        Y : 标签, shape=(batch_size, num_steps)
        """
        offset = random.randint(0, num_steps-1)  # 用随机偏移量划分序列
        num_tokens = ((len(corpus)-offset-1) // batch_size) * \
            batch_size  # 得到正好的token数, 将不能完整组成一批的token舍弃
        Xs = torch.tensor(corpus[offset: offset + num_tokens])
        Ys = torch.tensor(corpus[offset+1: offset + num_tokens + 1])
        Xs, Ys = Xs.reshape(batch_size, -1), Ys.reshape(batch_size, -1)
        num_batches = Xs.shape[1] // num_steps  # 小批量的个数(纵向分割出一个个batch)
        for i in range(0, num_batches*num_steps, num_steps):
            X = Xs[:, i:i + num_steps]  # 特征
            Y = Ys[:, i:i + num_steps]  # 标签
            yield X, Y  # shape=(batch_size, num_steps)

def load_time_machine_data(batch_size, num_steps,
                           max_tokens=10000, use_random_iter=False):
    """
    返回时光机器数据集的 迭代器、词表
    """
    data_iter = SeqDataLoader(batch_size, num_steps,
                              max_tokens, use_random_iter)
    return data_iter, data_iter.vocab

def predict_rnn(prefix, num_preds, net, vocab, device):
    """
    这个函数用于在prefix后面生成新字符\n
    prefix : 一个用户提供的包含多个字符的字符串\n
    在循环遍历prefix中的开始字符时,不断地将隐状态传递到下一个时间步，但是不生成任何输出。称为预热(warm-up)期,
    在此期间模型会自我更新(例如，更新隐状态),但不会进行预测。
    预热期结束后，隐状态的值通常比刚开始的初始值更适合预测，从而预测字符并输出它们。
    """
    state = net.begin_state(batch_size=1, device=device)
    outputs = [vocab[prefix[0]]]
    def get_input(): return torch.tensor(
        [outputs[-1]], device=device).reshape((1, 1))
    for y in prefix[1:]:  # 预热期
        _, state = net(get_input(), state)  # 更新隐状态
        outputs.append(vocab[y])
    for _ in range(num_preds):  # 预测num_preds步
        # 预测y并更新隐状态 相当于(batch_size, num_step)=(1,1)的单步预测
        y, state = net(get_input(), state)
        # argmax输出的是列表 所asynchronously reported at some other API call, so the stacktrace below might be incorrect.以需要reshape
        outputs.append(int(y.argmax(dim=1).reshape(1)))
    return ''.join([vocab.idx_to_token[i] for i in outputs])

def rnn_train_epoch(net, train_iter, loss_function, updater, device, use_random_iter):
    """
    训练模型的一个迭代周期\n
    当使用顺序分区时，只在每个迭代周期的开始位置初始化隐状态。
    由于下一个小批量数据中的第i个子序列样本与当前第i个子序列样本相邻,
    因此当前小批量数据最后一个样本的隐状态，将用于初始化下一个小批量数据第一个样本的隐状态。
    这样，存储在隐状态中的序列的历史信息可以在一个迭代周期内流经相邻的子序列。
    然而，在任何一点隐状态的计算，都依赖于同一迭代周期中前面所有的小批量数据，这使得梯度计算变得复杂。
    为了降低计算量，在处理任何一个小批量数据之前，要先分离梯度，使得隐状态的梯度计算总是限制在一个小批量数据的时间步内。
    (当使用随机抽样时,因为每个样本都是在一个随机位置抽样的,因此需要为每个迭代周期重新初始化隐状态。)
    """
    state, timer = None, Timer()
    metric = Accumulator(2)  # 训练损失之和 与 词元数量
    with timer:
        for X, Y in train_iter:
            if state is None or use_random_iter:  # 在第一次迭代或使用随机抽样时初始化state
                state = net.begin_state(batch_size=X.shape[0], device=device)
            else:  # 剥离梯度
                if isinstance(net, nn.Module) and not isinstance(state, tuple):  # state对于nn.GRU是个张量
                    state.detach_()
                else:  # state对于nn.LSTM或从头实现的模型是一个张量
                    for s in state:
                        s.detach_()
            y = Y.T.reshape(-1) # 展平为len=num_steps*batch_size的向量,以便nn.CrossEntropyLoss处理
            X, y = X.to(device), y.to(device)
            y_hat, state = net(X, state) # y_hat.shape=(num_steps*batch_size, vocab_size)
            loss = loss_function(y_hat, y.long()).mean() # .long将tensor的类型转化为torch.int64
            if isinstance(updater, torch.optim.Optimizer):
                updater.zero_grad()
                loss.backward()
                grad_clipping(net, theta=1)  # 梯度裁减
                updater.step()
            else:
                loss.backward()
                grad_clipping(net, theta=1)
                updater(batch_size=1)
            metric.add(loss*y.numel(), y.numel())
    
    return math.exp(metric[0]/metric[1]), metric[1]/timer.elapsed_time # 返回一次迭代的 困惑度 和 训练速度

def rnn_train(net, train_iter, vocab, lr, num_epochs, device, use_random_iter=False):
    """训练模型"""
    loss_function = nn.CrossEntropyLoss()
    res = ResVisualization(xlist=[[]], ylist=[[]], legend_names=['train'],
                           xlabel='epoch', ylabel='perplexity', title='train_res')
    if isinstance(net, nn.Module):
        updater = torch.optim.SGD(net.parameters(), lr)
    else:
        def updater(batch_size): return sgd(net.params, lr, batch_size)

    def predict(prefix): return predict_rnn(prefix, 50, net, vocab, device)
    for epoch in range(num_epochs):
        perplexity, train_speed = rnn_train_epoch(
            net, train_iter, loss_function, updater, device, use_random_iter)
        if (epoch+1) % 100 == 0:
            print(f"epoch: {epoch+1}, 对'time traveller'的预测:{predict(prefix='time traveller')}")
        res.add(epoch+1, perplexity, 'train')
    print(f"困惑度{perplexity:.2f}, {train_speed:.1f}词元/秒 在{str(device)}上")
    print(f"对prefix为'time traveller'的预测:{predict(prefix='time traveller')}")
    print(f"对prefix为'traveller'的预测:{predict(prefix='traveller')}")
    res.plot_res()
