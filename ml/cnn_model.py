import torch
import torch.nn as nn
import torch.nn.functional as F

class LogCNN(nn.Module):
    def __init__(self, vocab_size=12, embed_dim=16, seq_len=50):
        super().__init__()
        self.embed = nn.Embedding(vocab_size, embed_dim)
        self.conv1 = nn.Conv1d(embed_dim, 64, 3, padding=1)
        self.conv2 = nn.Conv1d(64, 128, 5, padding=2)
        self.conv3 = nn.Conv1d(128, 64, 7, padding=3)
        self.pool  = nn.AdaptiveMaxPool1d(1)
        self.drop  = nn.Dropout(0.3)
        self.fc1   = nn.Linear(64, 32)
        self.fc2   = nn.Linear(32, 1)

    def forward(self, x):
        x = self.embed(x).permute(0,2,1)
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = F.relu(self.conv3(x))
        x = self.pool(x).squeeze(-1)
        return torch.sigmoid(self.fc2(F.relu(self.fc1(self.drop(x))))).squeeze(-1)
