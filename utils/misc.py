import math
import pickle
from pathlib import Path
from time import time

import matplotlib.pyplot as plt
import numpy as np
import timm
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim
import torch.utils.data
import torchvision.transforms as T
from sklearn.metrics import f1_score, accuracy_score
from torch.utils.data import DataLoader, Dataset
from torchvision.utils import make_grid


class create_model:
    def __init__(self, initMode='default', verbose=True):
        self.val_dataloader = None
        self.test_dataloader = None
        self.train_dataloader = None
        return

    def run_model(self, fplankton, class_main):

        checkpoint_path = class_main.params.outpath + 'trained_models/'
        Path(checkpoint_path).mkdir(parents=True, exist_ok=True)

        train_dataset = AugmentedDataset(X=fplankton.X_train, y=fplankton.y_train)
        self.train_dataloader = DataLoader(train_dataset, class_main.params.batch_size, shuffle=True, num_workers=4,
                                           pin_memory=True)

        test_dataset = CreateDataset(X=fplankton.X_test, y=fplankton.y_test)
        self.test_dataloader = DataLoader(test_dataset, class_main.params.batch_size, shuffle=True, num_workers=4,
                                          pin_memory=True)

        val_dataset = CreateDataset(X=fplankton.X_val, y=fplankton.y_val)
        self.val_dataloader = DataLoader(val_dataset, class_main.params.batch_size, shuffle=True, num_workers=4,
                                         pin_memory=True)

        basemodel = timm.create_model('deit_base_patch16_224', pretrained=True,
                                      num_classes=len(np.unique(fplankton.y_train)))
        model = basemodel

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # model = nn.DataParallel(model)
        model.to(device)

        # total parameters and trainable parameters
        total_params = sum(p.numel() for p in model.parameters())
        print(f"{total_params:,} total parameters.")
        total_trainable_params = sum(
            p.numel() for p in model.parameters() if p.requires_grad)
        print(f"{total_trainable_params:,} training parameters.")

        criterion = nn.CrossEntropyLoss(fplankton.class_weights)

        # torch.cuda.set_device(class_main.params.gpu_id)
        # model.cuda(class_main.params.gpu_id)
        # criterion = criterion.cuda(class_main.params.gpu_id)

        # Observe that all parameters are being optimized
        optimizer = torch.optim.AdamW(model.parameters(), lr=class_main.params.lr,
                                      weight_decay=class_main.params.weight_decay)

        # Decay LR by a factor of 0.1 every 7 epochs
        # exp_lr_scheduler = lr_scheduler.StepLR(optimizer, step_size=7, gamma=0.1)

        # Early stopping and lr scheduler
        lr_scheduler = LRScheduler(optimizer)
        early_stopping = EarlyStopping()

        best_acc1 = 0
        best_f1 = 0
        train_losses, test_losses = [], []
        train_accuracies, test_accuracies = [], []
        train_f1s, test_f1s = [], []

        print("Beginning training")
        time_begin = time()

        for epoch in range(class_main.params.epochs):
            print('EPOCH : {} / {}'.format(epoch + 1, class_main.params.epochs))
            adjust_learning_rate(optimizer, epoch, class_main.params.lr, class_main.params.warmup, class_main.params.disable_cos,
                                 class_main.params.epochs)
            train_acc1, train_loss, train_outputs, train_targets = cls_train(self.train_dataloader, model, criterion,
                                                                             optimizer, class_main.params.clip_grad_norm)
            acc1, loss, test_outputs, test_targets, total_mins = cls_validate(self.val_dataloader, model, criterion
                                                                              , time_begin=time_begin)

            train_f1 = f1_score(train_outputs, train_targets, average='macro')
            train_accuracy = accuracy_score(train_outputs, train_targets)

            test_f1 = f1_score(test_outputs, test_targets, average='macro')
            test_accuracy = accuracy_score(test_outputs, test_targets)

            best_acc1 = max(acc1, best_acc1)

            if test_f1 > best_f1:
                torch.save({'model_state_dict': model.state_dict(),
                            'optimizer_state_dict': optimizer.state_dict()}, checkpoint_path + '/trained_model.pth')
            best_f1 = max(test_f1, best_f1)

            train_losses.append(train_loss)
            test_losses.append(loss)
            train_accuracies.append(train_accuracy)
            test_accuracies.append(test_accuracy)
            train_f1s.append(train_f1)
            test_f1s.append(test_f1)

            print('[Train] Acc:{}, F1:{}, loss:{}'.format(np.round(train_accuracy, 3),
                                                          np.round(train_f1, 3),
                                                          np.round(train_loss, 3),
                                                          np.round(test_accuracy, 3)))
            print('[Test] Acc:{}, F1:{}, loss:{}, TIME:{}'.format(np.round(test_accuracy, 3),
                                                                  np.round(test_f1, 3),
                                                                  np.round(loss, 3),
                                                                  np.round(total_mins, 3)))

            lr_scheduler(loss)
            early_stopping(loss)
            if early_stopping.early_stop:
                break

        total_mins = (time() - time_begin) / 60

        print(f'Script finished in {total_mins:.2f} minutes, '
              f'best acc top-1: {best_acc1:.2f}, '
              f'best f1 top-1: {best_f1:.2f}, '
              f'final top-1: {acc1:.2f}')
        # torch.save(model.state_dict(), checkpoint_path)

        Logs = [train_losses, train_accuracies, test_losses, test_accuracies, train_f1s, test_f1s]

        Log_Path = checkpoint_path

        with open(Log_Path + '/Logs.pickle', 'wb') as cw:
            pickle.dump(Logs, cw)


class AugmentedDataset(Dataset):
    """Characterizes a dataset for PyTorch"""

    def __init__(self, X, y):
        """Initialization"""
        self.X = X
        self.y = y

    def __len__(self):
        """Denotes the total number of samples"""
        return len(self.X)

    def __getitem__(self, index):
        """Generates one sample of data"""
        # Select sample
        image = self.X[index]
        label = self.y[index]
        X = self.transform(image)
        y = label
        sample = [X, y]
        return sample

    transform = T.Compose([
        T.ToPILImage(),
        T.Resize(224),
        T.RandomHorizontalFlip(),
        T.RandomVerticalFlip(),
        T.GaussianBlur(kernel_size=(3, 9), sigma=(0.1, 2)),
        #         T.RandomPerspective(distortion_scale=0.8, p=0.1),
        T.RandomRotation(degrees=(0, 180)),
        T.RandomAffine(degrees=(30, 90), translate=(0.1, 0.3), scale=(0.5, 0.9)),
        T.ToTensor()])
    transform_y = T.Compose([T.ToTensor()])


class CreateDataset(Dataset):
    """Characterizes a dataset for PyTorch"""

    def __init__(self, X, y):
        'Initialization'
        self.X = X
        self.y = y

    def __len__(self):
        'Denotes the total number of samples'
        return len(self.X)

    def __getitem__(self, index):
        'Generates one sample of data'
        # Select sample
        image = self.X[index]
        label = self.y[index]
        X = self.transform(image)
        y = label
        #         y = self.transform_y(label)
        #         sample = {'image': X, 'label': label}
        sample = [X, y]
        return sample

    transform = T.Compose([
        T.ToPILImage(),
        T.Resize(224),
        T.ToTensor()])
    transform_y = T.Compose([T.ToTensor()])


def adjust_learning_rate(optimizer, epoch, lr, warmup, disable_cos, epochs):
    lr = lr
    if epoch < warmup:
        lr = lr / (warmup - epoch)
    elif not disable_cos:
        lr *= 0.5 * (1. + math.cos(math.pi * (epoch - warmup) / (epochs - warmup)))

    for param_group in optimizer.param_groups:
        param_group['lr'] = lr


def show_images(data, nmax=64):
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.set_xticks([])
    ax.set_yticks([])
    print(data[1])
    ax.imshow(make_grid((data[0].detach()[:nmax]), nrow=8).permute(1, 2, 0))


def show_batch(dl, nmax=64):
    for images in dl:
        show_images(images, nmax)
        break


def accuracy(output, target):
    with torch.no_grad():
        batch_size = target.size(0)

        _, pred = output.topk(1, 1, True, True)
        pred = pred.t()
        correct = pred.eq(target.view(1, -1).expand_as(pred))

        res = []
        correct_k = correct[:1].flatten().float().sum(0, keepdim=True)
        res.append(correct_k.mul_(100.0 / batch_size))
        return res


class LRScheduler:
    """
    Learning rate scheduler. If the validation loss does not decrease for the
    given number of `patience` epochs, then the learning rate will decrease by
    by given `factor`.
    """

    def __init__(
            self, optimizer, patience=4, min_lr=1e-10, factor=0.5
    ):
        """
        new_lr = old_lr * factor
        :param optimizer: the optimizer we are using
        :param patience: how many epochs to wait before updating the lr
        :param min_lr: least lr value to reduce to while updating
        :param factor: factor by which the lr should be updated
        """
        self.optimizer = optimizer
        self.patience = patience
        self.min_lr = min_lr
        self.factor = factor
        self.lr_scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            self.optimizer,
            mode='min',
            patience=self.patience,
            factor=self.factor,
            min_lr=self.min_lr,
            verbose=True
        )

    def __call__(self, val_loss):
        self.lr_scheduler.step(val_loss)


class EarlyStopping:
    """
    Early stopping to stop the training when the loss does not improve after
    certain epochs.
    """

    def __init__(self, patience=5, min_delta=0):
        """
        :param patience: how many epochs to wait before stopping when loss is
               not improving
        :param min_delta: minimum difference between new loss and old loss for
               new loss to be considered as an improvement
        """
        self.patience = patience
        self.min_delta = min_delta
        self.counter = 0
        self.best_loss = None
        self.early_stop = False

    def __call__(self, val_loss):
        if self.best_loss is None:
            self.best_loss = val_loss
        elif self.best_loss - val_loss > self.min_delta:
            self.best_loss = val_loss
        elif self.best_loss - val_loss < self.min_delta:
            self.counter += 1
            print(f"INFO: Early stopping counter {self.counter} of {self.patience}")
            if self.counter >= self.patience:
                print('INFO: Early stopping')
                self.early_stop = True


def cls_train(train_loader, model, criterion, optimizer, clip_grad_norm):
    model.train()
    loss_val, acc1_val = 0, 0
    n = 0
    targets = []
    outputs = []

    for i, (images, target) in enumerate(train_loader):

        device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        images, target = images.to(device), target.to(device)

        # output, x = model(images)
        output = model(images)

        loss = criterion(output, target.long())

        acc1 = accuracy(output, target)

        n += images.size(0)
        loss_val += float(loss.item() * images.size(0))
        acc1_val += float(acc1[0] * images.size(0))

        optimizer.zero_grad()
        loss.backward()

        if clip_grad_norm > 0:
            nn.utils.clip_grad_norm_(model.parameters(), max_norm=clip_grad_norm, norm_type=2)

        optimizer.step()

        outputs.append(output)
        targets.append(target)

    outputs = torch.cat(outputs)
    outputs = outputs.cpu().detach().numpy()
    outputs = np.argmax(outputs, axis=1)

    targets = torch.cat(targets)
    targets = targets.cpu().detach().numpy()

    avg_loss, avg_acc1 = (loss_val / n), (acc1_val / n)
    return avg_acc1, avg_loss, outputs, targets


def cls_validate(val_loader, model, criterion, time_begin=None):
    model.eval()
    loss_val, acc1_val = 0, 0
    n = 0
    targets = []
    outputs = []

    with torch.no_grad():
        for i, (images, target) in enumerate(val_loader):
            device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
            images, target = images.to(device), target.to(device)

            output = model(images)
            # loss = criterion(output, target)
            loss = criterion(output, target.long())
            acc1 = accuracy(output, target)

            n += images.size(0)
            loss_val += float(loss.item() * images.size(0))
            acc1_val += float(acc1[0] * images.size(0))

            outputs.append(output)
            targets.append(target)

        outputs = torch.cat(outputs)
        outputs = outputs.cpu().detach().numpy()
        outputs = np.argmax(outputs, axis=1)

        targets = torch.cat(targets)
        targets = targets.cpu().detach().numpy()

    avg_loss, avg_acc1 = (loss_val / n), (acc1_val / n)
    total_mins = -1 if time_begin is None else (time() - time_begin) / 60
    return avg_acc1, avg_loss, outputs, targets, total_mins


def cls_predict(val_loader, model, criterion, time_begin=None):
    model.eval()
    loss_val, acc1_val = 0, 0
    n = 0
    outputs = []
    targets = []
    probs = []
    with torch.no_grad():
        for i, (images, target) in enumerate(val_loader):
            device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
            images, target = images.to(device), target.to(device)
            targets.append(target)

            output = model(images)
            outputs.append(output)
            prob = torch.nn.functional.softmax(output, dim=1)
            probs.append(prob)
            loss = criterion(output, target)

            acc1 = accuracy(output, target)
            n += images.size(0)
            loss_val += float(loss.item() * images.size(0))
            acc1_val += float(acc1[0] * images.size(0))

    avg_loss, avg_acc1 = (loss_val / n), (acc1_val / n)
    total_mins = -1 if time_begin is None else (time() - time_begin) / 60
    print('Time taken for prediction (in mins): {}'.format(total_mins))

    return avg_acc1, targets, outputs, probs
