import torch
import numpy as np

def stft(x, fft_size, hop_size, win_length, window):
    """Perform STFT and convert to magnitude spectrogram.
    Args:
        x (Tensor): Input signal tensor (B, T).
        fft_size (int): FFT size.
        hop_size (int): Hop size.
        win_length (int): Window length.
        window (str): Window function type.
    Returns:
        Tensor: Magnitude spectrogram (B, #frames, fft_size // 2 + 1).
    """
    x_stft = torch.stft(x, fft_size, hop_size, win_length, window)
    real = x_stft[..., 0]
    imag = x_stft[..., 1]

    return torch.sqrt(torch.clamp(real ** 2 + imag ** 2, min=1e-7)).transpose(2, 1)


class SpectralConvergenceLoss(torch.nn.Module):
    """Spectral convergence loss module.

    See [Arik et al., 2018](https://arxiv.org/abs/1808.06719). 
    """
    def __init__(self):
        """Initilize spectral convergence loss module."""
        super(SpectralConvergenceLoss, self).__init__()

    def forward(self, x_mag, y_mag):
        """Calculate forward propagation.
        Args:
            x_mag (Tensor): Magnitude spectrogram of predicted signal (B, #frames, #freq_bins).
            y_mag (Tensor): Magnitude spectrogram of groundtruth signal (B, #frames, #freq_bins).
        Returns:
            Tensor: Spectral convergence loss value.
        """
        return torch.norm(y_mag - x_mag, p="fro") / torch.norm(y_mag, p="fro")


class LogSTFTMagnitudeLoss(torch.nn.Module):
    """Log STFT magnitude loss module.
    
    See [Arik et al., 2018](https://arxiv.org/abs/1808.06719). 
    """
    def __init__(self):
        """Initilize los STFT magnitude loss module."""
        super(LogSTFTMagnitudeLoss, self).__init__()

    def forward(self, x_mag, y_mag):
        """Calculate forward propagation.
        Args:
            x_mag (Tensor): Magnitude spectrogram of predicted signal (B, #frames, #freq_bins).
            y_mag (Tensor): Magnitude spectrogram of groundtruth signal (B, #frames, #freq_bins).
        Returns:
            Tensor: Log STFT magnitude loss value.
        """
        return torch.nn.functional.l1_loss(torch.log(y_mag), torch.log(x_mag))


class STFTLoss(torch.nn.Module):
    """STFT loss module.
    
    See [Yamamoto et al. 2019](https://arxiv.org/abs/1904.04472).
    """
    def __init__(self, fft_size=1024, shift_size=120, win_length=600, window="hann_window"):
        """Initialize STFT loss module."""
        super(STFTLoss, self).__init__()
        self.fft_size = fft_size
        self.shift_size = shift_size
        self.win_length = win_length
        self.window = getattr(torch, window)(win_length)
        self.spectral_convergence_loss = SpectralConvergenceLoss()
        self.log_stft_magnitude_loss = LogSTFTMagnitudeLoss()

    def forward(self, x, y):
        """Calculate forward propagation.
        Args:
            x (Tensor): Predicted signal (B, T).
            y (Tensor): Groundtruth signal (B, T).
        Returns:
            Tensor: Spectral convergence loss value.
            Tensor: Log STFT magnitude loss value.
        """
        x_mag = stft(x, self.fft_size, self.shift_size, self.win_length, self.window)
        y_mag = stft(y, self.fft_size, self.shift_size, self.win_length, self.window)
        sc_loss = self.spectral_convergence_loss(x_mag, y_mag)
        mag_loss = self.log_stft_magnitude_loss(x_mag, y_mag)

        return sc_loss, mag_loss


class MultiResolutionSTFTLoss(torch.nn.Module):
    """Multi resolution STFT loss module.
    
    See [Yamamoto et al., 2019](https://arxiv.org/abs/1910.11480)
    """
    def __init__(self,
                 fft_sizes=[1024, 2048, 512],
                 hop_sizes=[120, 240, 50],
                 win_lengths=[600, 1200, 240],
                 window="hann_window"):
        """Initialize Multi resolution STFT loss module.
        Args:
            fft_sizes (list): List of FFT sizes.
            hop_sizes (list): List of hop sizes.
            win_lengths (list): List of window lengths.
            window (str): Window function type.
        """
        super(MultiResolutionSTFTLoss, self).__init__()
        assert len(fft_sizes) == len(hop_sizes) == len(win_lengths)
        self.stft_losses = torch.nn.ModuleList()
        for fs, ss, wl in zip(fft_sizes, hop_sizes, win_lengths):
            self.stft_losses += [STFTLoss(fs, ss, wl, window)]

    def forward(self, x, y):
        """Calculate forward propagation.
        Args:
            x (Tensor): Predicted signal (B, C, T).
            y (Tensor): Groundtruth signal (B, C, T).
        Returns:
            Tensor: Multi resolution spectral convergence loss value.
            Tensor: Multi resolution log STFT magnitude loss value.
        """
        sc_loss = 0.0
        mag_loss = 0.0
        for f in self.stft_losses:
            sc_l, mag_l = f(x, y)
            sc_loss += sc_l
            mag_loss += mag_l
        sc_loss /= len(self.stft_losses)
        mag_loss /= len(self.stft_losses)

        return sc_loss + mag_loss


class RandomResolutionSTFTLoss(torch.nn.Module):
    """Random resolution STFT loss module.

    Args:
        resolutions (int): Total number of STFT resolutions.
        min_fft_size (int): Smallest FFT size.
        max_fft_size (int): Largest FFT size.
        min_hop_size (int): Smallest hop size as porportion of window size.
        min_hop_size (int): Largest hop size as porportion of window size.
        window (str): Window function type.
        randomize_rate (int): Number of forwards before STFTs are randomized. 
    
    See [Yamamoto et al., 2019](https://arxiv.org/abs/1910.11480)
    """
    def __init__(self,
                 resolutions  = 3,
                 min_fft_size = 32,
                 max_fft_size = 16384,
                 min_hop_size = 0.25,
                 max_hop_size = 1.0,
                 window="hann_window",
                 randomize_rate = 1):
        super(RandomResolutionSTFTLoss, self).__init__()
        self.resolutions = resolutions
        self.min_fft_size = min_fft_size
        self.max_fft_size = max_fft_size
        self.min_hop_size = min_hop_size
        self.max_hop_size = max_hop_size
        self.window = window
        self.randomize_rate = randomize_rate

        self.nforwards = 0
        self.randomize_losses() # init the losses 

    def randomize_losses(self):

        # clear the existing STFT losses
        self.stft_losses = torch.nn.ModuleList()
        for n in range(self.resolutions):
            frame_size = 2 ** np.random.randint(np.log2(self.min_fft_size), np.log2(self.max_fft_size))
            hop_size = int(frame_size * (self.min_hop_size + (np.random.rand() * (self.max_hop_size-self.min_hop_size))))
            window_length = int(frame_size * np.random.choice([1.0, 0.5, 0.25]))
            self.stft_losses += [STFTLoss(frame_size, hop_size, window_length, self.window)]

    def forward(self, x, y):
        """Calculate forward propagation.
        Args:
            x (Tensor): Predicted signal (B, C, T).
            y (Tensor): Groundtruth signal (B, C, T).
        Returns:
            Tensor: Multi resolution spectral convergence loss value.
            Tensor: Multi resolution log STFT magnitude loss value.
        """
        sc_loss = 0.0
        mag_loss = 0.0
        for f in self.stft_losses:
            sc_l, mag_l = f(x.squeeze(), y.squeeze())
            sc_loss += sc_l
            mag_loss += mag_l
        sc_loss /= len(self.stft_losses)
        mag_loss /= len(self.stft_losses)

        self.nforwards += 1
        if self.nforwards % self.randomize_rate == 0:
            self.randomize_losses()

        return sc_loss + mag_loss

class SumAndDiffLoss(torch.nn.Module):
    """Sum and difference stereo balance loss module.
    
    See [Steinmetz et al., 2020](https://arxiv.org/abs/2010.10291)
    """
    def __init__(self):
        super(SumAndDiffLoss, self).__init__()

    def forward(input, target):
        pass