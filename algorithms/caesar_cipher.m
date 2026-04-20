% A simple Translation cipher for encrypting text by shifting charakters

%{
% Alphabetic version with modulo 26
function encrypted_text = caesar_cipher(text, shift)
    
    % Initialize en- and and convert unencrypted text to uppercase
    text = upper(text);
    encrypted_text = char(zeros(size(text)));

    % Loop over the message to shift charakters
    for i = 1:length(text)
        if text(i) >= 'A' && text(i) <= 'Z'
            % Shift characters using modulo to stay within the alphabet
            encrypted_text(i) = mod(text(i) - 'A' + shift, 26) + 'A';
        else
            % Skip non-alphabetic characters
            encrypted_text(i) = text(i);
        end
    end
end
%}

% Improved version using modulo 128 to shift all printable charakters
function encrypted_text = caesar_cipher(text, shift)
    
    % Check for banana iq
    if shift == 128 || shift == -128
        error('Choose another shift!');
    end

    % Check for 200 iq trolls
    if mod(shift, 128) == 0
    error('Choose another shift!');
    end
    
    % Initialize encrypted text
    encrypted_text = char(zeros(size(text)));

    % Loop over the message to shift charakters
    for i = 1:length(text)
        % Shift character and wrap using modulo 128
        encrypted_text(i) = mod(text(i) + shift, 128);
    end
end
