import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.cuda
from dynamicpool import DynamicPool

EMBEDDING_SIZE = 300
HIDDEN_SIZE = 200

LEARNING_RATE = 0.01
EPOCH_NUM = 100
BATCH_SIZE = 16
DROPOUT_RATE = 0.1
DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

TARGET_SIZE = 2
CONV_CHANNEL = 3
CONV_TARGET = 18

ENGLISH_TAG = 1  # 是否加入英语原语训练集，0：不加入；1：加入
ENGLISH_SPANISH_RATE = 1  # 英语原语训练数据与西班牙原语训练数据的比例
TRAINTEST_RATE = 0.7  # 划分训练集和验证集的比例

MAX_SQE_LEN = 56  # 最长的句子词数
END_OF_SEN = torch.ones(1, dtype=torch.float).new_full((1, EMBEDDING_SIZE), 0)


def initParameter(opt):
    global HIDDEN_SIZE, TARGET_SIZE, DROPOUT_RATE, LEARNING_RATE, BATCH_SIZE, \
        EPOCH_NUM, ENGLISH_TAG, ENGLISH_SPANISH_RATE, TRAINTEST_RATE, \
        DEVICE, MAX_SQE_LEN, CONV_CHANNEL, CONV_TARGET
    HIDDEN_SIZE = opt.hidden_size
    TARGET_SIZE = opt.target_size
    DROPOUT_RATE = opt.dropout_rate
    LEARNING_RATE = opt.learning_rate
    BATCH_SIZE = opt.batch_size
    EPOCH_NUM = opt.epoch_num
    ENGLISH_TAG = opt.english_tag
    ENGLISH_SPANISH_RATE = opt.english_spanish_rate
    TRAINTEST_RATE = opt.train_test_rate
    DEVICE = opt.device
    MAX_SQE_LEN = opt.max_sqe_len
    CONV_CHANNEL = opt.conv_channel
    CONV_TARGET = opt.conv_target


# 两个lstm网络模型
class Bi_LSTM(nn.Module):
    def __init__(self):
        super(Bi_LSTM, self).__init__()
        print('Current Model: Bi_LSTM')
        self.bi_lstm_context1 = nn.LSTM(EMBEDDING_SIZE, HIDDEN_SIZE, bidirectional=True)
        self.bi_lstm_context2 = nn.LSTM(EMBEDDING_SIZE, HIDDEN_SIZE, bidirectional=True)
        self.dense1 = nn.Linear(8 * HIDDEN_SIZE, 400)
        self.dense2 = nn.Linear(400, 100)
        self.dense3 = nn.Linear(100, TARGET_SIZE)

        self.dropout = nn.Dropout(DROPOUT_RATE)

        # self.stm = nn.Softmax(dim=0)
        self.stm = nn.Sigmoid()

    def forward(self, input1, input2):
        out1, (_, _) = self.bi_lstm_context1(input1)
        out2, (_, _) = self.bi_lstm_context2(input2)

        # 当batch_size > 1时，需要根据batch_size手动合并
        all_merge = []
        for idx in range(len(out1)):
            merge = torch.cat((out1[idx][0], out1[idx][-1], out2[idx][0], out2[idx][-1]), dim=0)
            if idx is 0:
                all_merge = merge.unsqueeze(0)
            else:
                all_merge = torch.cat((all_merge, merge.unsqueeze(0)), dim=0)

        out = self.dense1(all_merge)
        out = self.dense2(out)
        out = self.dense3(out)
        out = self.dropout(out)
        out = self.stm(out)
        return out


# 单向LSTM
class LSTM(nn.Module):
    def __init__(self):
        super(LSTM, self).__init__()
        print('Current model: LSTM')
        self.lstm1 = nn.LSTM(EMBEDDING_SIZE, HIDDEN_SIZE)
        self.lstm2 = nn.LSTM(EMBEDDING_SIZE, HIDDEN_SIZE)
        self.dense1 = nn.Linear(2 * HIDDEN_SIZE, 256)
        self.dense2 = nn.Linear(256, 50)
        self.dense3 = nn.Linear(50, TARGET_SIZE)

        self.dropout = nn.Dropout(DROPOUT_RATE)
        self.stm = nn.Softmax(dim=0)
        # self.stm = nn.Sigmoid()

    def forward(self, input1, input2):
        out1, hidden1 = self.lstm1(input1)
        out2, hidden2 = self.lstm2(input2)

        # 当batch_size > 1时，需要根据batch_size手动合并
        all_merge = []
        for idx in range(len(out1)):
            merge = torch.cat((out1[idx][-1], out2[idx][-1]), dim=0)
            if idx is 0:
                all_merge = merge.unsqueeze(0)
            else:
                all_merge = torch.cat((all_merge, merge.unsqueeze(0)), dim=0)

        # merge = torch.cat((out1[0][-1], out2[0][-1]), dim=0)
        out = self.dense1(all_merge)
        out = self.dense2(out)
        out = self.dense3(out)
        out = self.dropout(out)
        out = self.stm(out)
        return out


class MatchSRNN(nn.Module):
    def __init__(self):
        super(MatchSRNN, self).__init__()
        print('Current model: Match-SpatialRNN')
        self.dimension = 3
        self.hidden_dim = 3
        self.target = 2
        self.T = torch.nn.Parameter(torch.randn(self.dimension, 300, 300))
        self.Linear = nn.Linear(600, self.dimension)
        self.relu = nn.ReLU()
        self.qrLinear = nn.Linear(3 * self.hidden_dim + self.dimension, 3 * self.hidden_dim)
        self.qzLinear = nn.Linear(3 * self.hidden_dim + self.dimension, 4 * self.hidden_dim)
        self.U = torch.nn.Parameter(torch.randn(self.hidden_dim, 3 * self.hidden_dim))
        self.h_linear = nn.Linear(self.dimension, self.hidden_dim)
        self.tanh = nn.Tanh()
        self.lastlinear = nn.Linear(self.dimension, self.target)

    def getS(self, input1, input2):
        out = []
        for i in range(self.dimension):
            tmp = torch.mm(input1.view(1, -1), self.T[i])
            tmp = torch.mm(tmp, input2.view(-1, 1))
            out.append(tmp.item())
        add_input = torch.cat((input1.view(1, -1), input2.view(1, -1)), dim=1)
        lin = self.Linear(add_input)
        out = torch.add(torch.tensor(out), lin.view(-1))
        out = self.relu(out)
        return out.view(1, -1)

    def softmaxbyrow(self, input):
        # z1=input[:self.hidden_dim]
        # z2=input[self.hidden_dim:self.hidden_dim*2]
        # z3 = input[self.hidden_dim*2:self.hidden_dim * 3]
        # z4 = input[self.hidden_dim*3:self.hidden_dim * 4]
        input = input.view(4, -1)
        input = torch.transpose(input, 0, 1)
        a = []
        for i in range(self.hidden_dim):
            if i == 0:
                tmp = F.softmax(input[i], dim=0).view(1, -1)
            else:
                tmp = torch.cat((tmp, F.softmax(input[i], dim=0).view(1, -1)), dim=0)

        z1 = tmp[:, 0]
        z2 = tmp[:, 1]
        z3 = tmp[:, 2]
        z4 = tmp[:, 3]

        return z1, z2, z3, z4

    def spatialRNN(self, input_s, hidden):
        q = torch.cat((torch.cat((hidden[0], hidden[1])), torch.cat((hidden[2], input_s))))
        r = F.sigmoid(self.qrLinear(q))
        # print("q:",q)
        z = self.qzLinear(q)
        z1, z2, z3, z4 = self.softmaxbyrow(z)
        # print("r:",r)
        # print("qwe:",torch.cat((hidden[0], hidden[1], hidden[2])))
        # print("sd:",torch.mm(self.U,(r*torch.cat((hidden[0],hidden[1],hidden[2]))).view(-1,1)).view(-1))
        # print("fdsf:",self.h_linear(input_s))
        h_ = self.tanh(self.h_linear(input_s) + torch.mm(self.U,
                                                         (r * torch.cat((hidden[0], hidden[1], hidden[2]))).view(-1,
                                                                                                                 1)).view(
            -1))
        h = z2 * hidden[1] + z3 * hidden[0] + z4 * hidden[2] + h_ * z1
        # print(z2*hidden[1],z3*hidden[0],z4*hidden[2],h_*z1)
        # print("h",h)
        return h

    def init_hidden(self, all_hidden, i, j):
        if i == 0 and j == 0:
            return [torch.zeros(self.hidden_dim), torch.zeros(self.hidden_dim), torch.zeros(self.hidden_dim)]
        elif i == 0:
            return [torch.zeros(self.hidden_dim), all_hidden[i][j - 1], torch.zeros(self.hidden_dim)]
        elif j == 0:
            return [all_hidden[i - 1][j], torch.zeros(self.hidden_dim), torch.zeros(self.hidden_dim)]
        else:
            return all_hidden[i - 1][j], all_hidden[i][j - 1], all_hidden[i - 1][j - 1]

    def forward(self, input1, input2):
        count = 0
        for i in range(input1.size(0)):
            for j in range(input2.size(0)):
                if count == 0:
                    s = self.getS(input1[i], input2[j])
                    count += 1
                else:
                    s_ij = self.getS(input1[i], input2[j])
                    s = torch.cat((s, s_ij), dim=0)
        s = s.view(input1.size(0), input2.size(0), -1)
        all_hidden = [[] for i in range(input1.size(0))]
        for i in range(input1.size(0)):
            for j in range(input2.size(0)):
                # print(self.init_hidden(all_hidden,i,j))
                hidden = self.spatialRNN(s[i][j], self.init_hidden(all_hidden, i, j))
                all_hidden[i].append(hidden)
        # print(all_hidden)

        out = self.lastlinear(all_hidden[input1.size(0) - 1][input2.size(0) - 1])
        out = F.softmax(out, dim=0)
        return out


class Text2Image(nn.Module):
    def __init__(self):
        super(Text2Image, self).__init__()
        self.conv1 = nn.Conv2d(1, CONV_CHANNEL, 3, padding=0)
        self.conv2_1 = nn.Conv2d(1, 1, 3, padding=0)
        self.conv2_2 = nn.Conv2d(1, 1, 3, padding=0)
        self.conv2_3 = nn.Conv2d(1, 1, 3, padding=0)
        self.fc1 = nn.Linear(8 * 8 * 1, 30)
        # self.fc1 = nn.Linear(CONV_TARGET * CONV_TARGET * CHANNEL_SIZE, 100)
        self.fc2 = nn.Linear(30, 2)
        # self.fc3 = nn.Linear(30, TARGET_SIZE)
        self.dropout = nn.Dropout(DROPOUT_RATE)
        self.softmax = nn.Softmax(dim=1)
        self.target_pool = [CONV_TARGET, CONV_TARGET]

    def forward(self, sentence_1, sentence_2):
        matrix_x, size = DynamicPool.cal_similar_matrix(sentence_1, sentence_2)
        matrix_x.view(size, -1, 56, 56)
        matrix_x = F.relu(self.conv1(matrix_x))
        # 暂时用不到的动态pooling
        # origin_size1 = len(matrix_x[0][0])
        # origin_size2 = len(matrix_x[0][0][0])
        # # 填充卷积输出
        # # print(matrix_x)
        # while origin_size1 < self.target_pool[0]:
        #     matrix_x = torch.cat([matrix_x, matrix_x[:, :, :origin_size1, :]], dim=2)
        #     if len(matrix_x[0][0]) >= self.target_pool[0]:
        #         break
        #
        # while origin_size2 < self.target_pool[1]:
        #     matrix_x = torch.cat([matrix_x, matrix_x[:, :, :, :origin_size2]], dim=3)
        #     if len(matrix_x[0][0][0]) >= self.target_pool[1]:
        #         break
        #
        # dynamic_pool_size1 = len(matrix_x[0][0])
        # dynamic_pool_size2 = len(matrix_x[0][0][0])
        # get_index = DynamicPool(self.target_pool[0], self.target_pool[1], dynamic_pool_size1, dynamic_pool_size2)
        # index, pool_size = get_index.d_pool_index()
        # m, n, high_judge, weight_judge = get_index.cal(index)
        # stride = pool_size[0]
        # stride1 = pool_size[1]
        #
        # matrix_x1 = matrix_x[:, :, :m, :n]
        # matrix_x1 = F.max_pool2d(matrix_x1, (stride, stride1))
        #
        # if high_judge > 0:
        #     matrix_x2 = matrix_x[:, :, m:, :n]
        #     matrix_x2 = F.max_pool2d(matrix_x2, (stride + 1, stride1))
        # if weight_judge > 0:
        #     matrix_x3 = matrix_x[:, :, :m, n:]
        #     matrix_x3 = F.max_pool2d(matrix_x3, (stride, stride1 + 1))
        # if high_judge > 0 and weight_judge > 0:
        #     matrix_x4 = matrix_x[:, :, m:, n:]
        #     matrix_x4 = F.max_pool2d(matrix_x4, (stride + 1, stride1 + 1))
        #
        # if high_judge == 0 and weight_judge == 0:
        #     matrix_x = matrix_x1
        # elif high_judge > 0 and weight_judge == 0:
        #     matrix_x = torch.cat([matrix_x1, matrix_x2], dim=2)
        # elif high_judge == 0 and weight_judge > 0:
        #     matrix_x = torch.cat([matrix_x1, matrix_x3], dim=3)
        # else:
        #     matrix_x_1 = torch.cat([matrix_x1, matrix_x2], dim=2)
        #     matrix_x_2 = torch.cat([matrix_x3, matrix_x4], dim=2)
        #     matrix_x = torch.cat([matrix_x_1, matrix_x_2], dim=3)

        matrix_x = F.max_pool2d(matrix_x, (3, 3))
        need_matrix = matrix_x[:, 0, :, :].view(size, 1, CONV_TARGET, CONV_TARGET)
        mat2_1 = self.conv2_1(need_matrix)
        need_matrix = matrix_x[:, 1, :, :].view(size, 1, CONV_TARGET, CONV_TARGET)
        mat2_2 = self.conv2_1(need_matrix)
        need_matrix = matrix_x[:, 2, :, :].view(size, 1, CONV_TARGET, CONV_TARGET)
        mat2_3 = self.conv2_1(need_matrix)

        reshape_matrix = mat2_1 + mat2_2 + mat2_3

        reshape_matrix = self.dropout(reshape_matrix)
        reshape_matrix = F.max_pool2d(reshape_matrix, (2, 2))
        reshape_matrix = reshape_matrix.view(-1, self.num_flat_features(reshape_matrix))
        reshape_matrix = F.tanh(self.fc1(reshape_matrix))
        reshape_matrix = F.sigmoid(self.fc2(reshape_matrix))
        # matrix_x = self.fc3(matrix_x)
        reshape_matrix = reshape_matrix
        return reshape_matrix

    def num_flat_features(self, x):
        size = x.size()[1:]
        num_features = 1
        for s in size:
            num_features *= s
        return num_features
