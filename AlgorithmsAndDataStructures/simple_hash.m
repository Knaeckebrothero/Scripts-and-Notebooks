% A simple hash function
function hash = simple_hash(string)
    
    % Define a fixed output length of 10
    hash_values = [11 103 75 23 8 78 53 16 85 44];
    
    % Pre-calculate values that don't change inside the loop
    str_length = length(string);
    
    % Perform a bunch of complex operations to generate the hash
    for i = 1:10
        % Pick a char to start
        index = double(string(mod((i + 5) * 33, str_length) + 1));
        
        % Perform the operations to mix it up
        hash_values(i) = mod(str_length * (10 * sum(hash_values)^3 + sqrt((index + 41) * (sum(hash_values) + str_length + 7) + 131))^2 / 100, 128);
    end
    
    % Map to printable ascii chars
    for i = 1:10
        if hash_values(i) < 10
        % Map to numbers 0-9 (ASCII 48-57)
        hash_values(i) = hash_values(i) + 48;
        elseif hash_values(i) < 36
        % Map to uppercase A-Z (ASCII 65-90)
        hash_values(i) = hash_values(i) + 55;
        else
        % Map to lowercase a-z (ASCII 97-122)
        hash_values(i) = hash_values(i) + 61;
        end
    end

    % Convert the hash into a string of ASCII characters
    hash = char(hash_values);
end

%{
% A simple hash function
function hash = simple_hash(string)
    
    % Define a fixed output lenght of 10
    hash_values = [11 103 75 23 8 78 53 16 85 44];
    
    % Perform a bunch of fcked up operations to generate the hash
    for i = 1:10
        % mod(ts * (10 * h^3 + sqrt(((tn + 5) * 33 mod ts + 41) * (h + ts + 7) + 131))^2 / 100, 128)
        hash_values(i) = mod(length(string) * (10 * sum(hash_values)^3 + sqrt((string(mod((i + 5) * 33, length(string))) + 41) * (sum(hash_values) + length(string) + 7) + 131))^2 / 100, 128);
    end
    
    % Convert the hash into ascii
    hash = '';
    for i = 1:10
        hash = [hash, char(hash_values(i))];
    end
end
%}